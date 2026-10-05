#!/bin/sh
set -eu
curl -fsS http://127.0.0.1:8080/healthz >/dev/null
curl -fsS http://127.0.0.1:8090/healthz >/dev/null
supervisorctl -c /opt/service/supervisord.conf status | python3 -c '
import sys
states = [line.split()[1] for line in sys.stdin if line.strip()]
sys.exit(0 if len(states) == 3 and all(state == "RUNNING" for state in states) else 1)
'
