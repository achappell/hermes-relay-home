#!/bin/sh

set -eu
umask 077

pilot_root="${HOME}/.hermes/hermes-home-standard-pilot"
proxy_script="${pilot_root}/proxy.py"
log_file="${pilot_root}/proxy.log"
hermes_python="${HOME}/.hermes/hermes-agent/venv/bin/python"
token_file="${pilot_root}/standard-token"

if [ ! -r "$token_file" ]; then
    echo "Standard pilot token file is missing: $token_file" >&2
    exit 1
fi
if [ ! -r "$proxy_script" ]; then
    echo "Standard pilot relay is missing: $proxy_script" >&2
    exit 1
fi
if [ ! -x "$hermes_python" ]; then
    echo "Hermes Agent Python runtime is missing: $hermes_python" >&2
    exit 1
fi

export HERMES_STANDARD_PILOT_TOKEN_FILE="$token_file"
export HERMES_STANDARD_PILOT_PROXY_HOST=127.0.0.1
export HERMES_STANDARD_PILOT_PROXY_PORT=9121

exec >>"$log_file" 2>&1
printf '%s starting Standard pilot relay\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
exec "$hermes_python" "$proxy_script"
