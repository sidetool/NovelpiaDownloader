#!/usr/bin/env python3
"""Exercise unchanged image methods against the real CDN, without account access."""
import argparse
import pathlib
import secrets
import subprocess
import tempfile
import urllib.parse

root = pathlib.Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--container', default='novelpia-downloader')
parser.add_argument('--build-image', default='novelpia-downloader-build:local')
parser.add_argument('--image-url', required=True)
args = parser.parse_args()
url = urllib.parse.urlsplit(args.image_url)
if url.scheme != 'https' or url.hostname != 'images.novelpia.com':
    parser.error('--image-url must use https://images.novelpia.com/')
remote = '/tmp/novelpia-images-' + secrets.token_hex(8)

def run(*command):
    subprocess.run(command, check=True)

with tempfile.TemporaryDirectory(prefix='novelpia-images-') as temp:
    executable = pathlib.Path(temp) / 'images.exe'
    run('docker', 'run', '--rm',
        '-v', str(root / 'tests/images.cs') + ':/tests/images.cs:ro',
        '-v', temp + ':/test-output', '--entrypoint', 'mcs', args.build_image,
        '/tests/images.cs', '-r:/src/bin/Release/NovelpiaDownloader.exe',
        '-r:System.Windows.Forms', '-r:System.Web.Extensions', '-out:/test-output/images.exe')
    run('docker', 'exec', '-u', '1000', args.container, 'mkdir', '-m', '700', remote)
    try:
        run('docker', 'cp', str(executable), args.container + ':' + remote + '/images.exe')
        run('docker', 'exec', '-u', '1000', args.container,
            'curl', '-fsS', '--max-time', '30', '-o', remote + '/expected.bin', args.image_url)
        run('docker', 'exec', '-u', '1000', '-w', remote, '-e', 'MONO_PATH=/opt/novelpia',
            args.container, 'mono', remote + '/images.exe', args.image_url)
    finally:
        run('docker', 'exec', '-u', '1000', args.container, 'rm', '-rf', remote)
