#!/usr/bin/env python3
"""Exercise optimization in a disposable Docker service, never in the user's library."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

from optimization import fixture

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--image', default='novelpia-downloader-web:local')
parser.add_argument('--build-image', default='novelpia-downloader-build:local')
parser.add_argument('--browser', action='store_true')
args = parser.parse_args()
suffix = secrets.token_hex(6)
container = 'novelpia-opt-test-' + suffix
network = container + '-net'


def run(*command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


with tempfile.TemporaryDirectory(prefix='novelpia-opt-') as temp:
    folder = Path(temp); data = folder / 'data'; data.mkdir(mode=0o755)
    username, password = 'test', secrets.token_urlsafe(24)
    env_file = folder / 'test.env'
    env_file.write_text(f'WEB_USERNAME={username}\nWEB_PASSWORD={password}\n'); env_file.chmod(0o600)
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

        def request(path, payload=None, authenticated=True, csrf=True):
            headers = {'Authorization': auth} if authenticated else {}
            if payload is not None:
                headers['Content-Type'] = 'application/json'
                if csrf:
                    headers['X-Requested-With'] = 'Novelpia-Web'
            req = urllib.request.Request(base + path, headers=headers,
                data=json.dumps(payload).encode() if payload is not None else None)
            try:
                response = opener.open(req, timeout=10)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                body = response.read()
                try: value = json.loads(body)
                except ValueError: value = body.decode(errors='replace')
                return response.status, value

        def ready():
            for unused in range(120):
                try:
                    if request('/api/state')[0] == 200: return
                except OSError: pass
                time.sleep(.2)
            subprocess.run(['docker', 'logs', '--tail', '30', container], check=False)
            raise AssertionError('Disposable service did not start')

        def finished():
            for unused in range(300):
                snapshot = request('/api/state')[1]['optimization']
                if not snapshot['running']: return snapshot
                time.sleep(.1)
            raise AssertionError('Optimization did not finish')

        ready()
        assert request('/api/state')[1]['settings']['optimizeImages'] is False
        route = '/api/files/optimize'
        assert request(route, {}, authenticated=False)[0] == 401
        assert request(route, {}, csrf=False)[0] == 403
        assert request(route)[0] == 404
        protected = data / 'state/protected.epub'; protected.write_text('preserve')
        (data / 'downloads/linked.epub').symlink_to(protected)
        (data / 'downloads/test.txt').write_text('preserve txt')
        for name in ['../state/protected.epub', '..\\state\\protected.epub', 'linked.epub', 'test.txt', '', 123]:
            assert request(route, {'name': name})[0] == 400, name
        assert request(route, {'name': 'missing.epub'})[0] == 404
        assert request('/api/settings', {'settings': {'optimizeImages': 'true'}})[0] == 400
        assert request('/api/settings', {'settings': {'optimizeImages': True}})[0] == 200
        assert 'optimizeImages' not in json.loads((data / 'state/config.json').read_text())
        assert json.loads((data / 'state/web-settings.json').read_text()) == {'optimizeImages': True}
        run('docker', 'restart', container, stdout=subprocess.DEVNULL)
        port = subprocess.check_output(['docker', 'inspect', '--format',
            '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}', container], text=True).strip()
        base = 'http://127.0.0.1:' + port
        ready()
        assert request('/api/state')[1]['settings']['optimizeImages'] is True
        print('PASS: auth/CSRF/path/symlink/type validation, default and persisted independent option', flush=True)

        original = data / 'downloads' / ('API-' + suffix + '-한글 "따옴표" & 책.epub')
        contents = fixture(original)
        # Add enough illustrations to observe concurrent polling and busy guards reliably.
        with zipfile.ZipFile(original, 'r') as archive:
            entries = {info.filename: archive.read(info) for info in archive.infolist()}
        extras = []
        for index in range(24):
            entries[f'OEBPS/Images/extra{index}.png'] = contents['OEBPS/Images/삽화.png']
            extras.append(f'<item id="extra{index}" href="Images/extra{index}.png" media-type="image/png"/>')
        entries['OEBPS/content.opf'] = entries['OEBPS/content.opf'].replace(b'</manifest>', ''.join(extras).encode() + b'</manifest>')
        with zipfile.ZipFile(original, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries.items(): archive.writestr(name, content)
        before = hashlib.sha256(original.read_bytes()).digest()
        assert request(route, {'name': original.name})[0] == 200
        started = time.monotonic()
        assert request('/api/state')[1]['optimization']['running']
        assert time.monotonic() - started < 1
        for path, payload in [('/api/files/delete', {'name': original.name}), ('/api/files/clear', {}),
                              ('/api/download', {'settings': {'novelNumber': '0'}}), (route, {'name': original.name})]:
            assert request(path, payload)[0] == 409, path
        result = finished()
        assert result['status'] == 'complete', result
        outcome = result['results'][0]
        assert outcome['converted'] == 25 and outcome['changed'], outcome
        target = original.with_name(outcome['name'])
        assert target.exists() and target.stat().st_size < original.stat().st_size
        assert hashlib.sha256(original.read_bytes()).digest() == before
        assert outcome['name'] in {entry['name'] for entry in request('/api/files')[1]}
        assert not list((data / 'downloads').glob('.epub-opt-*'))
        print('PASS: async API, live progress, concurrent polling/busy guards and smaller original-preserving copy', flush=True)

        broken = data / 'downloads/broken.epub'; broken.write_text('invalid fixture')
        assert request(route, {'name': broken.name})[0] == 200
        assert finished()['status'] == 'error'
        assert broken.read_text() == 'invalid fixture'
        assert protected.read_text() == 'preserve'

        # Test the adapter's actual queue handlers and completion hooks with a local title server.
        executable = folder / 'optimization-flow.exe'
        run('docker', 'run', '--rm', '-v', str(ROOT / 'tests/optimization-flow.cs') + ':/tests/test.cs:ro',
            '-v', temp + ':/test-output', '--entrypoint', 'mcs', args.build_image,
            '/tests/test.cs', '-r:/src/bin/Release/NovelpiaDownloader.exe', '-r:System.Windows.Forms',
            '-out:/test-output/optimization-flow.exe')
        remote = '/tmp/optimization-flow-' + suffix
        run('docker', 'exec', '-u', '1000', container, 'mkdir', '-m', '700', remote)
        run('docker', 'cp', str(executable), container + ':' + remote + '/test.exe')
        flow_book = data / 'downloads/flow-fixture.epub'; fixture(flow_book)
        run('docker', 'exec', '-u', '1000', '-w', remote, '-e', 'MONO_PATH=/opt/novelpia',
            container, 'mono', remote + '/test.exe', '/data/downloads/' + flow_book.name)

        if args.browser:
            ui_name = 'UI-' + suffix + '-최적화 검증.epub'; fixture(data / 'downloads' / ui_name)
            environment = dict(os.environ, BASE_URL=base, TEST_ENV_FILE=str(env_file),
                OPTIMIZATION_TEST='1', OPTIMIZATION_FIXTURE=ui_name)
            run('node', str(ROOT / 'tests/browser.cjs'), env=environment)
    finally:
        subprocess.run(['docker', 'rm', '-f', container], stdout=subprocess.DEVNULL, check=False)
        subprocess.run(['docker', 'network', 'rm', network], stdout=subprocess.DEVNULL, check=False)
