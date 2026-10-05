#!/bin/sh
set -eu
umask 022
while ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; do sleep 0.2; done
cd /data/state
mono /opt/novelpia/NovelpiaWebHost.exe &
app_pid=$!
stop_app() {
    /opt/service/stop-app.sh || true
    attempts=0
    while kill -0 "$app_pid" 2>/dev/null && [ "$attempts" -lt 40 ]; do
        sleep 0.2
        attempts=$((attempts + 1))
    done
    kill -TERM "$app_pid" 2>/dev/null || true
    wait "$app_pid" 2>/dev/null || true
}
trap stop_app TERM INT
wait "$app_pid"
