#!/bin/sh

set -eu
umask 077

pilot_root="${HOME}/.hermes/hermes-home-standard-pilot"
proxy_script="${pilot_root}/proxy.py"
diagnostics_module="${pilot_root}/hermes_home_diagnostics.py"
diagnostics_directory="${HERMES_HOME_PROXY_LOG_DIR:-${pilot_root}/logs}"
hermes_python="${HOME}/.hermes/hermes-agent/venv/bin/python"
token_file="${pilot_root}/standard-token"

if [ ! -r "$token_file" ]; then
    echo "Standard pilot token file is unavailable" >&2
    exit 1
fi
if [ ! -r "$proxy_script" ]; then
    echo "Standard pilot relay is unavailable" >&2
    exit 1
fi
if [ ! -r "$diagnostics_module" ]; then
    echo "Standard pilot diagnostics helper is unavailable" >&2
    exit 1
fi
if [ ! -x "$hermes_python" ]; then
    echo "Hermes Agent Python runtime is unavailable" >&2
    exit 1
fi

export HERMES_STANDARD_PILOT_TOKEN_FILE="$token_file"
export HERMES_STANDARD_PILOT_PROXY_HOST=127.0.0.1
export HERMES_STANDARD_PILOT_PROXY_PORT=9121
export HERMES_HOME_PROXY_LOG_DIR="$diagnostics_directory"

exec "$hermes_python" "$proxy_script"
