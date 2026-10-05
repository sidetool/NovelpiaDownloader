#!/usr/bin/env python3
"""Test deletion only in a new container with disposable files, never user data."""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--image', default='novelpia-downloader-web:local')
parser.add_argument('--build-image', default='novelpia-downloader-build:local')
parser.add_argument('--browser', action='store_true')
args = parser.parse_args()
suffix = secrets.token_hex(6)
container = 'novelpia-files-test-' + suffix
network = container + '-net'

def run(*command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)

with tempfile.TemporaryDirectory(prefix='novelpia-files-') as temp:
    folder = Path(temp)
    data = folder / 'data'
    data.mkdir(mode=0o755)
    username, password = 'test', secrets.token_urlsafe(24)
    env_file = folder / 'test.env'
    env_file.write_text(f'WEB_USERNAME={username}\nWEB_PASSWORD={password}\n')
    env_file.chmod(0o600)
    auth = 'Basic ' + base64.b64encode(f'{username}:{password}'.encode()).decode()
    run('docker', 'network', 'create', network, stdout=subprocess.DEVNULL)
    try:
        run('docker', 'run', '-d', '--name', container, '--network', network,
            '--env-file', str(env_file), '-p', '127.0.0.1::8080', '-v', str(data) + ':/data',
            '--shm-size', '256m', args.image, stdout=subprocess.DEVNULL)
        port = subprocess.check_output(['docker', 'inspect', '--format',
            '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}', container], text=True).strip()
        base = 'http://127.0.0.1:' + port
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request(path, data=None, authenticated=True, csrf=True):
            headers = {'Authorization': auth} if authenticated else {}
            if data is not None:
                headers['Content-Type'] = 'application/json'
                if csrf:
                    headers['X-Requested-With'] = 'Novelpia-Web'
            req = urllib.request.Request(base + path, headers=headers,
                data=json.dumps(data).encode() if data is not None else None)
            try:
                response = opener.open(req, timeout=10)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                body = response.read()
                try:
                    value = json.loads(body)
                except ValueError:
                    value = body.decode(errors='replace')
                return response.status, value

        for attempt in range(100):
            try:
                if request('/api/state')[0] == 200:
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
        else:
            raise AssertionError('Disposable service did not start')

        first = '삭제 검증 "따옴표" & 한글 ' + suffix + '.epub'
        second = '삭제 검증 ' + suffix + '.TXT'
        fixture_code = '''
from pathlib import Path
import sys
p=Path('/data/downloads')
for name in sys.argv[1:]: (p/name).write_text('test book')
(p/'readme.json').write_text('keep json')
(p/'work').mkdir()
(p/'work/cache.txt').write_text('keep nested file')
target=Path('/data/state/protected.txt'); target.write_text('keep outside file')
(p/'linked.txt').symlink_to(target)
'''
        run('docker', 'exec', '-u', '1000', container, 'python3', '-c', fixture_code, first, second)
        before = (data / 'state/config.json').read_bytes()
        status, files = request('/api/files')
        assert status == 200 and {file['name'] for file in files} == {first, second}, files
        for route in ['/api/files/delete', '/api/files/clear']:
            assert request(route, {}, authenticated=False)[0] == 401
            assert request(route, {}, csrf=False)[0] == 403
            assert request(route)[0] == 404
        assert request('/api/files', authenticated=False)[0] == 401
        for name in ['', '../state/protected.txt', '..\\state\\protected.txt',
                     '/data/state/protected.txt', 'work/cache.txt', 'readme.json', 'linked.txt', 123]:
            assert request('/api/files/delete', {'name': name})[0] == 400, name
        assert request('/api/files/delete', {'name': 'missing.txt'})[0] == 404
        print('PASS: authenticated deletion, CSRF guard, path validation and symlink protection', flush=True)

        executable = folder / 'file-busy.exe'
        run('docker', 'run', '--rm', '-v', str(ROOT / 'tests/file-busy.cs') + ':/tests/file-busy.cs:ro',
            '-v', temp + ':/test-output', '--entrypoint', 'mcs', args.build_image,
            '/tests/file-busy.cs', '-r:/src/bin/Release/NovelpiaDownloader.exe',
            '-r:System.Windows.Forms', '-out:/test-output/file-busy.exe')
        remote = '/tmp/file-busy-' + suffix
        run('docker', 'exec', '-u', '1000', container, 'mkdir', '-m', '700', remote)
        run('docker', 'cp', str(executable), container + ':' + remote + '/file-busy.exe')
        run('docker', 'exec', '-u', '1000', '-w', remote, '-e', 'MONO_PATH=/opt/novelpia',
            container, 'mono', remote + '/file-busy.exe', first)

        status, result = request('/api/files/delete', {'name': first})
        assert status == 200 and result['deleted'] == 1, result
        assert not (data / 'downloads' / first).exists() and (data / 'downloads' / second).exists()
        status, result = request('/api/files/clear', {})
        assert status == 200 and result['deleted'] == 1, result
        assert request('/api/files') == (200, [])
        assert request('/api/files/clear', {})[1]['deleted'] == 0
        assert (data / 'state/config.json').read_bytes() == before
        assert (data / 'state/protected.txt').read_text() == 'keep outside file'
        assert (data / 'downloads/readme.json').read_text() == 'keep json'
        assert (data / 'downloads/work/cache.txt').read_text() == 'keep nested file'
        assert (data / 'downloads/linked.txt').is_symlink()
        print('PASS: single/all deletion preserves settings, outside targets and temporary files', flush=True)

        if args.browser:
            prefix = 'UI-' + suffix + '-'
            names = [prefix + '한글 "따옴표" & 첫 파일.epub', prefix + '두 번째 파일.TXT',
                     prefix + '긴파일명' * 10 + '.txt']
            run('docker', 'exec', '-u', '1000', container, 'python3', '-c',
                'from pathlib import Path; import sys; [(Path("/data/downloads")/name).write_text("test book") for name in sys.argv[1:]]', *names)
            environment = dict(os.environ, BASE_URL=base, TEST_ENV_FILE=str(env_file),
                FILE_DELETE_TEST='1', FILE_FIXTURE_PREFIX=prefix)
            run('node', str(ROOT / 'tests/browser.cjs'), env=environment)
    finally:
        subprocess.run(['docker', 'rm', '-f', container], stdout=subprocess.DEVNULL, check=False)
        subprocess.run(['docker', 'network', 'rm', network], stdout=subprocess.DEVNULL, check=False)
