#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ -e .env ]; then
    echo '.env already exists; keeping existing credentials.'
    exit 0
fi
umask 077
python3 - <<'PY'
from pathlib import Path
import secrets
template = Path('.env.example').read_text()
Path('.env').write_text(template.replace('replace-with-a-long-random-password', secrets.token_urlsafe(24)))
print('Created .env with username admin and a random password. Read .env to sign in.')
PY
