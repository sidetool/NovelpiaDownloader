#!/bin/sh
set -eu
: "${WEB_USERNAME:=admin}"
: "${WEB_PASSWORD:?WEB_PASSWORD is required}"
case "$WEB_USERNAME" in
    ''|*[!a-zA-Z0-9_.-]*) echo 'WEB_USERNAME must contain only letters, numbers, _, . or -.' >&2; exit 1 ;;
esac
if [ "${#WEB_PASSWORD}" -lt 12 ]; then
    echo 'WEB_PASSWORD must be at least 12 characters.' >&2
    exit 1
fi
umask 077
mkdir -p /data/state /data/downloads /run/novelpia
chown novelpia:novelpia /data/state /data/downloads
chmod 700 /data/state
chmod 755 /data/downloads
printf '%s\n' "$WEB_PASSWORD" | htpasswd -i -B -c /run/novelpia/htpasswd "$WEB_USERNAME" >/dev/null 2>&1
chown root:www-data /run/novelpia/htpasswd
chmod 640 /run/novelpia/htpasswd
chmod 755 /run/novelpia
if [ ! -f /data/state/config.json ]; then
    printf '{"language":"ko","output_dir":"/data/downloads"}\n' > /data/state/config.json
    chown novelpia:novelpia /data/state/config.json
fi
# The original app saves config.json in its current working directory on close.
exec /usr/bin/supervisord -n -c /opt/service/supervisord.conf
