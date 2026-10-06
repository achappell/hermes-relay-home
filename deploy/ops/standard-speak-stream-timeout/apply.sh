#!/bin/sh
# Apply the Standard speak-stream timeout patch to the hermes-agent checkout.
#
# Idempotent and non-destructive:
#   * already applied            -> reports it and exits 0 without touching files
#   * applies cleanly            -> backs up the two target files, applies, exits 0
#   * conflicts with the checkout -> changes nothing, exits 2
# It never commits, stashes or restarts anything. Restart the Standard pilot
# yourself, only while idle (the command is printed at the end).
#
# Usage: sh apply.sh [--check]
#   --check   report the state only; never back up or modify anything
# Environment:
#   HERMES_AGENT_DIR   checkout root (default: ~/.hermes/hermes-agent)
#   BACKUP_ROOT        backup parent (default: ~/hermes-backups)
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
PATCH="$HERE/standard-speak-stream-timeout.patch"
ROOT="${HERMES_AGENT_DIR:-$HOME/.hermes/hermes-agent}"
BACKUP_ROOT="${BACKUP_ROOT:-$HOME/hermes-backups}"
FILES="hermes_cli/web_routers/audio.py tools/tts_streaming.py"
RESTART='launchctl kickstart -k gui/$(id -u)/com.hermes.home-standard-pilot'

sha256() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1"; else sha256sum "$1"; fi
}

check_only=0
case "${1:-}" in
  "") ;;
  --check) check_only=1 ;;
  *) echo "usage: sh apply.sh [--check]" >&2; exit 64 ;;
esac

[ -f "$PATCH" ] || { echo "missing patch: $PATCH" >&2; exit 66; }
git -C "$ROOT" rev-parse --show-toplevel >/dev/null 2>&1 \
  || { echo "not a git checkout: $ROOT (set HERMES_AGENT_DIR)" >&2; exit 66; }
[ "$(git -C "$ROOT" rev-parse --show-toplevel)" = "$(cd "$ROOT" && pwd -P)" ] \
  || { echo "$ROOT is not the repository root" >&2; exit 66; }

# `git apply` run from the repository root resolves the patch's a/ b/ paths.
cd "$ROOT"

if git apply --check --reverse "$PATCH" >/dev/null 2>&1; then
  echo "already applied: speak-stream timeout patch is present in $ROOT"
  echo "nothing changed; HEAD $(git rev-parse --short=12 HEAD)"
  exit 0
fi

if ! err=$(git apply --check "$PATCH" 2>&1); then
  echo "CONFLICT: the patch does not apply to $ROOT (HEAD $(git rev-parse --short=12 HEAD))" >&2
  echo "$err" | sed 's/^/  /' >&2
  echo "Nothing was changed. Upstream may have touched the same lines or already" >&2
  echo "fixed the stall; compare tools/tts_streaming.py and" >&2
  echo "hermes_cli/web_routers/audio.py against the patch, then re-derive it by hand." >&2
  echo "See README.md ('Conflicts') for the checklist." >&2
  exit 2
fi

if [ "$check_only" = 1 ]; then
  echo "not applied, but it would apply cleanly to $ROOT (HEAD $(git rev-parse --short=12 HEAD))"
  exit 0
fi

stamp=$(date +%Y%m%d-%H%M%S)
backup="$BACKUP_ROOT/standard-speak-timeout-$stamp"
mkdir -p "$backup"
: >"$backup/original.sha256"
for f in $FILES; do
  mkdir -p "$backup/$(dirname "$f")"
  cp -p "$f" "$backup/$f"
  sha256 "$f" >>"$backup/original.sha256"
done

git apply "$PATCH"
echo "applied: speak-stream timeout patch to $ROOT (HEAD $(git rev-parse --short=12 HEAD))"
echo "originals backed up in $backup"
echo "NOT restarted. When the Standard pilot is idle, run:"
echo "  $RESTART"
