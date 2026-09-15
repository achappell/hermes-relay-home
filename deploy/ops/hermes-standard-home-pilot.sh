#!/bin/sh

set -eu
umask 077

pilot_root="${HOME}/.hermes/hermes-home-standard-pilot"
token_file="${pilot_root}/standard-token"
log_file="${pilot_root}/standard.log"
hermes_python="${HOME}/.hermes/hermes-agent/venv/bin/python"
profile="${HERMES_HOME_STANDARD_PROFILE:-amanda}"

if [ ! -r "$token_file" ]; then
    echo "Standard pilot token file is missing: $token_file" >&2
    exit 1
fi
if [ ! -x "$hermes_python" ]; then
    echo "Hermes Agent Python runtime is missing: $hermes_python" >&2
    exit 1
fi

token="$(<"$token_file")"
if [ -z "$token" ]; then
    echo "Standard pilot token file is blank: $token_file" >&2
    exit 1
fi

exec >>"$log_file" 2>&1
printf '%s starting Standard pilot for profile %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$profile"
export HERMES_DASHBOARD_SESSION_TOKEN="$token"
exec "$hermes_python" -m hermes_cli.main -p "$profile" serve \
    --port 9120 \
    --host 127.0.0.1 \
    --skip-build \
    --isolated
