#!/usr/bin/env python3
"""Verify the native web bridge; --exercise is for disposable test instances."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--base-url', default='http://127.0.0.1:8797')
parser.add_argument('--connect-address')
parser.add_argument('--container', default='novelpia-downloader')
parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
parser.add_argument('--exercise', action='store_true')
args = parser.parse_args()
base = args.base_url.rstrip('/')
url = urllib.parse.urlsplit(base)
if args.connect_address:
    original_connect = socket.create_connection
    def direct_connect(address, *values, **kwargs):
        host, port = address
        return original_connect((args.connect_address if host == url.hostname else host, port), *values, **kwargs)
    socket.create_connection = direct_connect
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
env = dict(line.split('=', 1) for line in args.env_file.read_text().splitlines() if line and not line.startswith('#'))
auth = 'Basic ' + base64.b64encode((env['WEB_USERNAME'] + ':' + env['WEB_PASSWORD']).encode()).decode()


def request(path, authenticated=True, headers=None, data=None):
    values = {'Authorization': auth} if authenticated else {}
    if data is not None:
        values.update({'Content-Type': 'application/json', 'X-Requested-With': 'Novelpia-Web'})
    values.update(headers or {})
    req = urllib.request.Request(base + path, headers=values, data=json.dumps(data).encode() if data is not None else None)
    try:
        response = opener.open(req, timeout=120)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


def state():
    result = request('/api/state')
    assert result[0] == 200, result[2]
    return json.loads(result[2])


def post(path, data):
    result = request(path, data=data)
    assert result[0] == 200, (path, result[0], result[2])
    return json.loads(result[2])


for line in (ROOT / 'docker/upstream.sha256').read_text().splitlines():
    expected, name = line.split('  ', 1)
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
print('PASS: original source, project and resources are unchanged')

for path in ['/', '/app.js', '/files/', '/api/state', '/api/settings', '/api/files', '/api/files/delete', '/api/files/clear']:
    assert request(path, False)[0] == 401, path
wrong = 'Basic ' + base64.b64encode(b'admin:wrong-password').decode()
assert request('/', headers={'Authorization': wrong})[0] == 401
for path in ['/', '/app.js', '/style.css', '/favicon.ico']:
    assert request(path)[0] == 200, path
for path in ['/config.json', '/state/config.json', '/files/%2e%2e/state/config.json', '/.env', '/internal/close', '/novnc/core/rfb.js', '/websockify', '/imagebox/cover/test.file']:
    assert request(path)[0] == 404, path
assert request('/healthz', False)[0] == 200
assert request('/api/settings', data={'settings': {}}, headers={'X-Requested-With': ''})[0] == 403
current = state()
assert 'progress' in current and 'queue' in current and 'log' in current
assert 'password' not in current and 'loginKey' not in current
bad = {'settings': {'threadNum': current['limits']['threadNum']['max'] + 1}}
assert request('/api/settings', data=bad)[0] == 400
assert request('/api/download', data={'settings': {'novelNumber': 'invalid'}})[0] == 400
assert request('/api/queue/remove', data={'indices': [-1]})[0] == 400
assert request('/api/files/delete', data={'name': '../state/config.json'})[0] == 400
assert request('/api/files/clear', data={}, headers={'X-Requested-With': ''})[0] == 403
print('PASS: web/API authentication, CSRF guard, native state and input validation')

name = '웹 검증 ' + secrets.token_hex(6) + '.txt'
fixture = '/data/downloads/' + name
payload = 'Docker 서비스 파일 다운로드 검증\n'.encode()
try:
    subprocess.run(['docker', 'exec', '-i', '-u', '1000', args.container, 'python3', '-c',
                    'import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.write_bytes(sys.stdin.buffer.read()); p.chmod(0o644)', fixture], input=payload, check=True)
    listing = request('/files/')
    assert listing[0] == 200 and name in [item['name'] for item in json.loads(listing[2])]
    listing = request('/api/files')
    assert listing[0] == 200 and name in [item['name'] for item in json.loads(listing[2])]
    path = '/files/' + urllib.parse.quote(name)
    result = request(path)
    assert result[0] == 200 and result[2] == payload
    assert result[1]['Content-Disposition'] == 'attachment'
    assert request(path, False)[0] == 401
    assert request(path, headers={'Range': 'bytes=0-5'})[2] == payload[:6]
finally:
    subprocess.run(['docker', 'exec', '-u', '1000', args.container, 'python3', '-c',
                    'import pathlib,sys; pathlib.Path(sys.argv[1]).unlink(missing_ok=True)', fixture], check=True)
print('PASS: private config, Korean filenames, file and ranged downloads')

if args.exercise:
    assert not current['running'] and not current['queue'], 'Use an idle disposable instance'
    key = 'test-only-' + secrets.token_hex(12)
    post('/api/login', {'mode': 'key', 'loginKey': key})
    assert key not in request('/api/state')[2].decode(), 'LOGINKEY leaked'
    options = dict(current['settings'])
    options.update({'novelNumber': '0', 'format': 'txt', 'threadNum': 2, 'interval': 0.1,
                    'retry': 1, 'fromEnabled': True, 'from': 2, 'toEnabled': True, 'to': 3,
                    'bonusNever': False, 'bonusAlways': True, 'keepHtml': False})
    post('/api/settings', {'settings': options})
    assert state()['settings'] == options
    saved = json.loads(subprocess.check_output(['docker', 'exec', args.container, 'cat', '/data/state/config.json']))
    assert saved['thread_num'] == 2 and saved['interval_num'] == 0.1 and saved['loginkey'] == key
    post('/api/queue/add', {'settings': options})
    queue = state()['queue']
    assert len(queue) == 1 and '(2-3)' in queue[0]['label'] and 'txt' in queue[0]['label']
    post('/api/queue/add', {'settings': options})
    assert len(state()['queue']) == 1, 'Original duplicate rule was not preserved'
    assert state()['log'], 'Original queue log was not surfaced'
    post('/api/queue/remove', {'indices': [0]})
    assert not state()['queue']
    post('/api/queue/clear', {})
    post('/api/queue/start', {})
    post('/api/stop', {})
    assert not state()['running']
    print('PASS: original LOGINKEY handler, config serialization, queue, duplicate rule, logs and removal')

subprocess.run(['docker', 'exec', args.container, '/opt/service/healthcheck.sh'], check=True)
print('PASS: native bridge and all service processes are running')
