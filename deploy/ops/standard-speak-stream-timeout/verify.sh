#!/bin/sh
# Read-only check that the speak-stream timeout patch is still in place.
# Run after every `hermes update`. Changes nothing; exit 0 = patched and loaded,
# exit 1 = patch missing/partial/conflicted, exit 3 = patched on disk but the
# running pilot predates the edit (restart needed).
#
# Environment:
#   HERMES_AGENT_DIR  checkout root (default: ~/.hermes/hermes-agent)
#   PILOT_LABEL       launchd label (default: com.hermes.home-standard-pilot)
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
PATCH="$HERE/standard-speak-stream-timeout.patch"
ROOT="${HERMES_AGENT_DIR:-$HOME/.hermes/hermes-agent}"
PILOT_LABEL="${PILOT_LABEL:-com.hermes.home-standard-pilot}"
TTS="$ROOT/tools/tts_streaming.py"
AUDIO="$ROOT/hermes_cli/web_routers/audio.py"
status=0

git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1 \
  || { echo "FAIL not a git checkout: $ROOT (set HERMES_AGENT_DIR)"; exit 1; }

echo "checkout: $ROOT  HEAD $(git -C "$ROOT" rev-parse --short=12 HEAD)"

echo "--- loaded source ---"
for pattern in 'stream_timeout_seconds' 'max_retries=0'; do
  if grep -q "$pattern" "$TTS" 2>/dev/null; then
    echo "ok   tools/tts_streaming.py contains $pattern"
  else
    echo "FAIL tools/tts_streaming.py lacks $pattern"; status=1
  fi
done
if grep -q 'type(exc).__name__' "$AUDIO" 2>/dev/null; then
  echo "ok   hermes_cli/web_routers/audio.py logs the exception type only"
else
  echo "FAIL hermes_cli/web_routers/audio.py lacks the type-only warning"; status=1
fi

echo "--- patch state ---"
if [ -f "$PATCH" ]; then
  if (cd "$ROOT" && git apply --check --reverse "$PATCH") >/dev/null 2>&1; then
    echo "ok   checked-in patch is fully present"
  elif (cd "$ROOT" && git apply --check "$PATCH") >/dev/null 2>&1; then
    echo "FAIL patch is absent but applies cleanly"; status=1
  else
    echo "FAIL patch is partial or conflicts with the checkout"; status=1
  fi
else
  echo "skip patch file not found next to this script ($PATCH)"
fi

echo "--- after-update state ---"
stashes=$(git -C "$ROOT" stash list | grep 'hermes-update-autostash-' || true)
if [ -n "$stashes" ]; then
  echo "note hermes-update autostash entries (a failed restore parks the patch here; older"
  echo "     entries from earlier updates are usually unrelated - check the stamp):"
  echo "$stashes" | sed 's/^/       /'
else
  echo "ok   no hermes-update autostash entries"
fi
unmerged=$(git -C "$ROOT" diff --name-only --diff-filter=U)
if [ -n "$unmerged" ]; then
  echo "FAIL unmerged paths:"; echo "$unmerged" | sed 's/^/       /'; status=1
fi
for f in tools/tts_streaming.py hermes_cli/web_routers/audio.py; do
  if grep -q '^<<<<<<< ' "$ROOT/$f" 2>/dev/null; then
    echo "FAIL conflict markers in $f"; status=1
  fi
done
if git -C "$ROOT" status --short -- tools/tts_streaming.py hermes_cli/web_routers/audio.py | grep -q .; then
  echo "ok   both files show as locally modified"
else
  echo "FAIL both files are clean: the update dropped the patch"; status=1
fi

echo "--- running process ---"
pid=$(launchctl print "gui/$(id -u)/$PILOT_LABEL" 2>/dev/null | sed -n 's/^[[:space:]]*pid = \([0-9][0-9]*\)$/\1/p' | head -n 1)
if [ -n "$pid" ]; then
  started=$(ps -o lstart= -p "$pid" 2>/dev/null | sed 's/^ *//; s/ *$//')
  start_epoch=$(date -j -f '%a %b %e %T %Y' "$started" +%s 2>/dev/null || true)
  mtime=$(stat -f %m "$TTS" 2>/dev/null || stat -c %Y "$TTS" 2>/dev/null || true)
  mtime2=$(stat -f %m "$AUDIO" 2>/dev/null || stat -c %Y "$AUDIO" 2>/dev/null || true)
  newest=$mtime
  [ -n "$mtime2" ] && [ "${mtime2:-0}" -gt "${newest:-0}" ] && newest=$mtime2
  if [ -n "$start_epoch" ] && [ -n "$newest" ]; then
    if [ "$start_epoch" -ge "$newest" ]; then
      echo "ok   pilot pid $pid started ($started) after the last edit"
    else
      echo "WARN pilot pid $pid started ($started) before the files last changed;"
      echo "     it is running the previous code. Restart only when idle:"
      echo '       launchctl kickstart -k gui/$(id -u)/'"$PILOT_LABEL"
      [ "$status" = 0 ] && status=3
    fi
  else
    echo "skip could not compare process start with file mtimes"
  fi
else
  echo "skip no running pid for $PILOT_LABEL"
fi

if [ "$status" = 1 ]; then
  echo "--- re-apply ---"
  echo "Re-apply with the checked-in script (idempotent; it reports CONFLICT without changing files):"
  echo "  sh \"$HERE/apply.sh\""
  echo "or by hand:"
  echo "  cd \"$ROOT\" && git apply \"$PATCH\""
  echo "A conflict means upstream changed the same lines; see README.md ('Conflicts')."
  echo "Then restart only when idle:"
  echo '  launchctl kickstart -k gui/$(id -u)/'"$PILOT_LABEL"
fi
exit "$status"
