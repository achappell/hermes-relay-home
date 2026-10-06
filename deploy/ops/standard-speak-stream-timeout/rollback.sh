#!/bin/sh
# Remove the Standard speak-stream timeout patch (reverse-apply).
#
# Reverse-applying the checked-in patch is exact only while the two files still
# carry the patch and nothing else has changed those lines. If it refuses, restore
# the backup apply.sh printed instead (see README.md, 'Rollback').
# This does NOT restart anything; restart yourself, only while idle.
#
# Environment: HERMES_AGENT_DIR (default: ~/.hermes/hermes-agent)
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
PATCH="$HERE/standard-speak-stream-timeout.patch"
ROOT="${HERMES_AGENT_DIR:-$HOME/.hermes/hermes-agent}"
RESTART='launchctl kickstart -k gui/$(id -u)/com.hermes.home-standard-pilot'

[ -f "$PATCH" ] || { echo "missing patch: $PATCH" >&2; exit 66; }
git -C "$ROOT" rev-parse --show-toplevel >/dev/null 2>&1 \
  || { echo "not a git checkout: $ROOT (set HERMES_AGENT_DIR)" >&2; exit 66; }
cd "$ROOT"

if git apply --check "$PATCH" >/dev/null 2>&1; then
  echo "already rolled back: the patch is not present in $ROOT"
  exit 0
fi
if ! git apply --check --reverse "$PATCH" >/dev/null 2>&1; then
  echo "cannot reverse-apply cleanly: the files differ from the patched state." >&2
  echo "Restore from the apply.sh backup directory instead; nothing was changed." >&2
  exit 2
fi

git apply --reverse "$PATCH"
echo "rolled back: speak-stream timeout patch removed from $ROOT"
echo "NOT restarted. When the Standard pilot is idle, run:"
echo "  $RESTART"
