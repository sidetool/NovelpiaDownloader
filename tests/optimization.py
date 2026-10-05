#!/usr/bin/env python3
"""EPUB correctness and image quality regressions, using only generated fixtures."""
import hashlib
import importlib.util
import io
from pathlib import Path
import random
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image, ImageCms, PngImagePlugin

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('optimizer', ROOT / 'docker/optimize_epub.py')
optimizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(optimizer)


def image_bytes(image, format='PNG', **options):
    out = io.BytesIO()
    image.save(out, format, **options)
    return out.getvalue()


def artwork():
    rand = random.Random(711)
    image = Image.new('RGB', (640, 960))
    image.putdata([(max(0, min(255, int(30 + x * .25 + rand.randrange(-2, 3)))),
                    max(0, min(255, int(50 + y * .15 + rand.randrange(-2, 3)))),
                    max(0, min(255, int(70 + (x + y) * .07 + rand.randrange(-2, 3)))))
                   for y in range(960) for x in range(640)])
    return image


def fixture(path):
    rgb = artwork()
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    opaque = image_bytes(rgb.convert('RGBA'), compress_level=1, icc_profile=profile)
    transparent = rgb.convert('RGBA'); transparent.putpixel((10, 10), (10, 20, 30, 0))
    frames = [rgb, rgb.transpose(Image.Transpose.FLIP_LEFT_RIGHT)]
    assets = {
        'OEBPS/Images/삽화.png': opaque,
        'OEBPS/Images/삽화.jpg': image_bytes(rgb, 'JPEG', quality=90),
        'OEBPS/Images/transparent.png': image_bytes(transparent, compress_level=1),
        'OEBPS/Images/animation.png': image_bytes(frames[0], save_all=True, append_images=frames[1:], duration=100, loop=0),
        'OEBPS/Images/cover.gif': image_bytes(frames[0], 'GIF', save_all=True, append_images=frames[1:], duration=100, loop=0),
        'OEBPS/Images/tiny.png': image_bytes(Image.new('RGB', (16, 16), 'white')),
        'OEBPS/Images/16bit.png': image_bytes(Image.new('I', (512, 512), 50000)),
    }
    manifest = ''.join('<item id="i%d" href="%s" media-type="%s"/>' %
        (index, optimizer.quote(name.removeprefix('OEBPS/'), safe='/'), 'image/jpeg' if name.endswith('.jpg') else 'image/gif' if name.endswith('.gif') else 'image/png')
        for index, name in enumerate(assets))
    opf = ('<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
        '<metadata><meta name="cover" content="i0"/></metadata><manifest>' + manifest +
        '<item id="chapter" href="Text/chapter.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="style" href="Styles/style.css" media-type="text/css"/>'
        '</manifest><spine><itemref idref="chapter"/></spine><guide><reference type="cover" href="Text/chapter.xhtml"/></guide></package>')
    chapter = ('<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head>'
        '<style>body { background: url("../Images/%EC%82%BD%ED%99%94.png"); }</style></head><body>'
        '<p>본문 문자 ../Images/삽화.png 및 url(../Images/삽화.png)는 그대로.</p>'
        '<img src="../Images/%EC%82%BD%ED%99%94.png?x=1&amp;y=2#view" alt="삽화"/>'
        '<img src="../Images/transparent.png"/><img src="../Images/animation.png"/></body></html>')
    contents = {
        'mimetype': b'application/epub+zip',
        'META-INF/container.xml': b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
        'OEBPS/content.opf': opf.encode(), 'OEBPS/Text/chapter.xhtml': chapter.encode(),
        'OEBPS/Styles/style.css': b'body { background: url(../Images/%EC%82%BD%ED%99%94.png#view); }',
        **assets,
    }
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    return contents


class OptimizationTests(unittest.TestCase):
    def test_epub_references_quality_and_preservation(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / '한글 "따옴표" & 책.epub'
            contents = fixture(source)
            original_hash = hashlib.sha256(source.read_bytes()).digest()
            progress = []
            result = optimizer.optimize(source, progress.append)
            self.assertTrue(result['changed'], result)
            self.assertEqual(result['converted'], 1)
            self.assertGreaterEqual(result['minPsnr'], optimizer.MIN_PSNR)
            self.assertLess(result['optimizedBytes'], result['originalBytes'])
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), original_hash)
            self.assertEqual(progress[-1]['done'], progress[-1]['total'])
            target = source.with_name(result['name'])
            with zipfile.ZipFile(target) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.infolist()[0].filename, 'mimetype')
                self.assertEqual(archive.infolist()[0].compress_type, zipfile.ZIP_STORED)
                new_name = 'OEBPS/Images/삽화.optimized-1.jpg'
                self.assertIn(new_name, archive.namelist())
                self.assertNotIn('OEBPS/Images/삽화.png', archive.namelist())
                for name, data in contents.items():
                    if name.startswith('OEBPS/Images/') and not name.endswith('삽화.png'):
                        self.assertEqual(archive.read(name), data, name)
                image = Image.open(io.BytesIO(archive.read(new_name)))
                self.assertEqual(image.size, (640, 960))
                self.assertEqual(image.layer, [(1, 1, 1, 0), (2, 1, 1, 1), (3, 1, 1, 1)])
                original = Image.open(io.BytesIO(contents['OEBPS/Images/삽화.png']))
                self.assertEqual(image.info['icc_profile'], original.info['icc_profile'])
                package = ET.fromstring(archive.read('OEBPS/content.opf'))
                original_package = ET.fromstring(contents['OEBPS/content.opf'])
                ns = {'o': 'http://www.idpf.org/2007/opf', 'h': 'http://www.w3.org/1999/xhtml'}
                for node in package.findall('o:manifest/o:item', ns):
                    self.assertIn(optimizer.member_path('OEBPS/content.opf', node.attrib['href']), archive.namelist())
                self.assertEqual(package.find('o:manifest/o:item', ns).attrib['media-type'], 'image/jpeg')
                self.assertEqual(ET.tostring(package.find('o:spine', ns)), ET.tostring(original_package.find('o:spine', ns)))
                chapter = ET.fromstring(archive.read('OEBPS/Text/chapter.xhtml'))
                original_chapter = ET.fromstring(contents['OEBPS/Text/chapter.xhtml'])
                self.assertEqual(list(chapter.find('h:body', ns).itertext()), list(original_chapter.find('h:body', ns).itertext()))
                image_url = chapter.find('h:body/h:img', ns).attrib['src']
                self.assertEqual(optimizer.member_path('OEBPS/Text/chapter.xhtml', image_url), new_name)
                self.assertTrue(image_url.endswith('?x=1&y=2#view'))
                self.assertIn(b'optimized-1.jpg#view', archive.read('OEBPS/Styles/style.css'))
                self.assertIn('optimized-1.jpg', chapter.find('h:head/h:style', ns).text)
            # Repeated optimization cannot reencode JPEG, make duplicate copies or change quality.
            again = optimizer.optimize(target)
            self.assertFalse(again['changed'])
            self.assertEqual(len(list(Path(folder).glob('*.epub'))), 2)
            # A new run of the original never overwrites an existing optimized copy.
            second = optimizer.optimize(source)
            self.assertNotEqual(second['name'], result['name'])
            self.assertEqual(len(list(Path(folder).glob('.epub-opt-*'))), 0)

    def test_preserve_noise_and_nonstandard_gamma(self):
        rand = random.Random(31)
        noise = Image.frombytes('RGB', (320, 320), rand.randbytes(320 * 320 * 3))
        data = image_bytes(noise, compress_level=0)
        self.assertIsNone(optimizer.jpeg_candidate(data, len(data)))
        gamma = PngImagePlugin.PngInfo(); gamma.add(b'gAMA', struct.pack('>I', 50000))
        data = image_bytes(artwork(), pnginfo=gamma)
        self.assertIsNone(optimizer.jpeg_candidate(data, len(data)))

    def test_invalid_book_does_not_publish_or_modify(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'broken.epub'
            source.write_bytes(b'not an EPUB')
            with self.assertRaises(zipfile.BadZipFile):
                optimizer.optimize(source)
            self.assertEqual(source.read_bytes(), b'not an EPUB')
            self.assertEqual(list(Path(folder).iterdir()), [source])
            fixture(source)
            with zipfile.ZipFile(source, 'a') as archive:
                archive.writestr('../outside.txt', b'bad path')
            with self.assertRaises(ValueError):
                optimizer.optimize(source)
            self.assertEqual(list(Path(folder).iterdir()), [source])


if __name__ == '__main__':
    unittest.main(verbosity=2)
