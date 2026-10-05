#!/usr/bin/env python3
"""Optional, local-only postprocessing. Never modify the downloaded original."""
import copy
import html
import io
import json
import math
import os
from pathlib import Path
import posixpath
import re
import shutil
import signal
import stat
import tempfile
from urllib.parse import quote, unquote, urlsplit, urlunsplit
import warnings
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image, ImageChops, ImageFile, ImageStat

QUALITY = 95
MIN_PSNR = 42.0
DOWNLOADS = Path('/data/downloads')
ATTR = re.compile(r'''(?P<name>[\w:-]+)\s*=\s*(?P<q>["'])(?P<value>.*?)(?P=q)''', re.S)
TAG = re.compile(r'<[A-Za-z_][^>]*>', re.S)
CSS_URL = re.compile(r'''url\(\s*(?P<q>["']?)(?P<value>[^)"']+)(?P=q)\s*\)''', re.I)


def member_path(document, url):
    parts = urlsplit(html.unescape(url))
    if parts.scheme or parts.netloc or not parts.path:
        return None
    return posixpath.normpath(posixpath.join(posixpath.dirname(document), unquote(parts.path))).lstrip('/')


def replacement_url(document, value, replacements):
    target = member_path(document, value)
    if target not in replacements:
        return value
    parts = urlsplit(html.unescape(value))
    path = posixpath.relpath(replacements[target], posixpath.dirname(document) or '.')
    if parts.path.startswith('/'):
        path = '/' + replacements[target]
    elif parts.path.startswith('./') and not path.startswith('.'):
        path = './' + path
    return urlunsplit(('', '', quote(path, safe='/'), parts.query, parts.fragment))


def rewrite_document(name, data, replacements):
    """Change resource attributes only; keep chapter text and markup as written."""
    text = data.decode('utf-8')

    def tag_replace(tag_match):
        tag = tag_match.group(0)
        attributes = {m['name']: m['value'] for m in ATTR.finditer(tag)}
        is_image_item = bool(re.match(r'<(?:\w+:)?item\b', tag)) and member_path(name, attributes.get('href', '')) in replacements

        def attr_replace(match):
            value = match['value']
            if match['name'] in ('href', 'src', 'data', 'poster', 'xlink:href'):
                new = replacement_url(name, value, replacements)
                if new != value:
                    return match.group(0).replace(value, html.escape(new, quote=True), 1)
            elif match['name'] == 'media-type' and is_image_item:
                return match.group(0).replace(value, 'image/jpeg', 1)
            return match.group(0)

        return ATTR.sub(attr_replace, tag)

    text = TAG.sub(tag_replace, text)
    # Covers external stylesheets and inline CSS without changing their layout.
    def css_replace(match):
        new = replacement_url(name, match['value'].strip(), replacements)
        return 'url(' + match['q'] + new + match['q'] + ')' if new != match['value'].strip() else match.group(0)
    if name.lower().endswith('.css'):
        text = CSS_URL.sub(css_replace, text)
    else:
        text = re.sub(r'<style\b[^>]*>.*?</style>', lambda match: CSS_URL.sub(css_replace, match.group(0)), text, flags=re.I | re.S)
        text = re.sub(r'''\bstyle\s*=\s*(["']).*?\1''', lambda match: CSS_URL.sub(css_replace, match.group(0)), text, flags=re.I | re.S)
    return text.encode('utf-8')


def jpeg_candidate(data, compressed_size):
    # Existing JPEG/WebP/GIF bytes are never decoded/recompressed.
    if len(data) < 65536 or not data.startswith(b'\x89PNG\r\n\x1a\n') or data[24] > 8:
        return None
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            if image.format != 'PNG' or getattr(image, 'n_frames', 1) != 1:
                return None
            image.load()
            if image.mode not in ('RGB', 'RGBA', 'L', 'LA', 'P') or image.getexif().get(274, 1) != 1:
                return None
            if 'gamma' in image.info and not image.info.get('icc_profile') and abs(image.info['gamma'] - 0.45455) > 0.001:
                return None
            rgba = image.convert('RGBA')
            if rgba.getchannel('A').getextrema() != (255, 255):
                return None
            rgb = image.convert('RGB')
            output = io.BytesIO()
            previous_block = ImageFile.MAXBLOCK
            try:
                # Pillow 9.x underestimates the optimized 4:4:4 buffer for noisy images.
                ImageFile.MAXBLOCK = max(previous_block, rgb.width * rgb.height * 4 +
                    len(image.info.get('icc_profile', b'')) + len(image.info.get('exif', b'')) + 1024)
                rgb.save(output, 'JPEG', quality=QUALITY, subsampling=0, optimize=True,
                         icc_profile=image.info.get('icc_profile'), exif=image.info.get('exif', b''),
                         **({'dpi': image.info['dpi']} if 'dpi' in image.info else {}))
            finally:
                ImageFile.MAXBLOCK = previous_block
            candidate = output.getvalue()
            if len(candidate) >= compressed_size - max(4096, compressed_size * 0.02):
                return None
            with Image.open(io.BytesIO(candidate)) as decoded:
                rms = ImageStat.Stat(ImageChops.difference(rgb, decoded)).rms
                mse = sum(value * value for value in rms) / 3
                psnr = 10 * math.log10(255 ** 2 / mse) if mse else 100.0
            # Preserve difficult line art/noise if even this high quality loses too much.
            if psnr < MIN_PSNR:
                return None
            return candidate, psnr


def read_xml(archive, name):
    if archive.getinfo(name).file_size > 16 * 1024 * 1024:
        raise ValueError('EPUB 문서가 너무 큽니다.')
    data = archive.read(name)
    if b'<!ENTITY' in data.upper():
        raise ValueError('지원하지 않는 EPUB XML입니다.')
    return ET.fromstring(data)


def package_images(archive):
    if archive.read('mimetype') != b'application/epub+zip':
        raise ValueError('올바른 EPUB 파일이 아닙니다.')
    if {'META-INF/encryption.xml', 'META-INF/signatures.xml'} & set(archive.namelist()):
        raise ValueError('암호화되거나 서명된 EPUB은 최적화할 수 없습니다.')
    container = read_xml(archive, 'META-INF/container.xml')
    packages = [node.attrib['full-path'] for node in container.iter() if node.tag.endswith('}rootfile')]
    if not packages:
        raise ValueError('EPUB 패키지 정보가 없습니다.')
    images = set()
    for package in packages:
        for node in read_xml(archive, package).iter():
            if node.tag.endswith('}item') and node.attrib.get('media-type') == 'image/png':
                path = member_path(package, node.attrib.get('href', ''))
                if path is not None:
                    archive.getinfo(path)  # Fail before creating a copy if references are broken.
                    images.add(path)
    return packages, sorted(images)


def output_name(source):
    stem = source.stem
    while len(stem.encode('utf-8')) > 210:
        stem = stem[:-1]
    for index in range(1, 10001):
        suffix = ' [최적화]' if index == 1 else ' [최적화] (' + str(index) + ')'
        target = source.with_name(stem + suffix + '.epub')
        if not os.path.lexists(target):
            return target
    raise ValueError('최적화본 파일 이름을 만들 수 없습니다.')


def optimize(source, emit=lambda event: None):
    source = Path(source)
    if source.suffix.lower() != '.epub':
        raise ValueError('EPUB 파일만 이미지 최적화할 수 있습니다.')
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as original:
        before = os.fstat(original.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('일반 EPUB 파일만 최적화할 수 있습니다.')
        with zipfile.ZipFile(original) as archive, tempfile.TemporaryDirectory(prefix='.epub-opt-', dir=source.parent) as work:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            if len(names) != len(entries) or any('\\' in name or name.startswith('/') or
                any(part in ('', '.', '..') for part in name.rstrip('/').split('/')) or
                posixpath.normpath(name.rstrip('/')) != name.rstrip('/') for name in names):
                raise ValueError('EPUB 내부 파일 경로가 올바르지 않습니다.')
            packages, images = package_images(archive)
            replacements, staged, scores = {}, {}, []
            total = len(images)
            for index, name in enumerate(images):
                emit(dict(event='progress', done=index, total=total))
                entry = archive.getinfo(name)
                candidate = None
                if entry.file_size <= 64 * 1024 * 1024:
                    try:
                        candidate = jpeg_candidate(archive.read(name), entry.compress_size)
                    except InterruptedError:
                        raise
                    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
                        pass  # Unsupported or damaged images keep their exact original bytes.
                if candidate:
                    new_name = posixpath.splitext(name)[0] + '.jpg'
                    serial = 1
                    while new_name in names:
                        new_name = posixpath.splitext(name)[0] + '.optimized-' + str(serial) + '.jpg'
                        serial += 1
                    names.add(new_name)
                    staged[name] = Path(work) / (str(index) + '.jpg')
                    staged[name].write_bytes(candidate[0])
                    replacements[name] = new_name
                    scores.append(candidate[1])
            emit(dict(event='progress', done=total, total=total))
            result = dict(event='result', source=source.name, originalBytes=before.st_size,
                          optimizedBytes=before.st_size, converted=len(replacements), total=total, changed=False)
            if not replacements:
                return result
            temporary = Path(work) / 'book.epub'
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
                ordered = [archive.getinfo('mimetype')] + [entry for entry in entries if entry.filename != 'mimetype']
                for entry in ordered:
                    target = copy.copy(entry)
                    target.filename = replacements.get(entry.filename, entry.filename)
                    target.extra = b''  # Do not copy stale ZIP64 sizes or Unicode-path fields.
                    if entry.filename == 'mimetype':
                        target.compress_type = zipfile.ZIP_STORED
                        output.writestr(target, b'application/epub+zip')
                    elif entry.filename in staged:
                        target.compress_type = zipfile.ZIP_STORED
                        output.writestr(target, staged[entry.filename].read_bytes())
                    elif entry.filename in packages or entry.filename.lower().endswith(('.html', '.xhtml', '.xml', '.css', '.ncx', '.svg')):
                        if entry.file_size > 16 * 1024 * 1024:
                            raise ValueError('EPUB 문서가 너무 큽니다.')
                        output.writestr(target, rewrite_document(entry.filename, archive.read(entry), replacements))
                    else:
                        with archive.open(entry) as src, output.open(target, 'w') as dst:
                            shutil.copyfileobj(src, dst, 1024 * 1024)
                output.comment = archive.comment
            # Check MIME, manifest and ZIP integrity before publishing the new file.
            with zipfile.ZipFile(temporary) as check:
                if check.testzip() is not None:
                    raise ValueError('최적화 EPUB 검증에 실패했습니다.')
                for package in packages:
                    for node in read_xml(check, package).iter():
                        if node.tag.endswith('}item'):
                            path = member_path(package, node.attrib.get('href', ''))
                            if path is not None:
                                check.getinfo(path)
                                if path in replacements.values() and node.attrib.get('media-type') != 'image/jpeg':
                                    raise ValueError('최적화 이미지 형식 검증에 실패했습니다.')
            after = os.fstat(original.fileno())
            if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                raise ValueError('원본 파일이 변경되어 최적화를 중단했습니다.')
            size = temporary.stat().st_size
            if size >= before.st_size:
                result['converted'] = 0
                return result
            with temporary.open('rb') as ready:
                os.fsync(ready.fileno())
            destination = output_name(source)
            # Atomic, exclusive publication; existing files/symlinks are never overwritten.
            os.link(temporary, destination)
            destination.chmod(0o644)
            result.update(changed=True, name=destination.name, optimizedBytes=size, minPsnr=round(min(scores), 2))
            return result


def main():
    def emit(event):
        print(json.dumps(event, ensure_ascii=False), flush=True)
    def interrupted(*unused):
        raise InterruptedError('최적화가 중단되었습니다. 원본 파일은 유지됩니다.')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        name = json.loads(input()).get('name')
        if not isinstance(name, str) or not name or any(char in name for char in ('/', '\\', '\0')):
            raise ValueError('파일 이름이 올바르지 않습니다.')
        if DOWNLOADS.is_symlink():
            raise ValueError('저장 폴더가 올바르지 않습니다.')
        emit(optimize(DOWNLOADS / name, emit))
    except Exception as error:
        emit(dict(event='error', message=str(error)))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
