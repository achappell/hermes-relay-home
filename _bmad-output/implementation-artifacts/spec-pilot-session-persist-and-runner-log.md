---
title: 'Pilot bridge — persist the session only after an accepted turn, and keep the Windows runner alive'
type: 'bugfix'
created: '2026-09-16'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Two defects blocked the STD-4 live pilot on 2026-09-16.

1. `HomeBridge` persisted Standard's durable session ID onto the conversation
   grant as soon as `conversation.open` (and `reconnect`) succeeded. Hermes only
   stores a session once it has a message. When the endpoint disconnected
   before any turn — which the iOS activation handshake always does — Standard
   reaped the orphaned session (`ws_orphan_reap`) and every later open sent
   `session.resume` for an ID Hermes never stored, so the grant returned
   `unavailable` / `request_rejected` permanently.
2. `deploy/windows/run.ps1` ran Python with `>> log 2>&1` under
   `$ErrorActionPreference = 'Stop'`. In Windows PowerShell 5.1 the first
   stderr line from a native process becomes a terminating
   `NativeCommandError`: the runner exited 1 and Home died, while the log stayed
   empty because redirected stdout was buffered. Reproduced on CaticornQueen.

**Approach:** Keep a newly created session's durable ID in memory for in-process
reconnect, and write it to the grant only after Standard accepts the first
prompt, so a reopen before any turn creates a fresh session. Grants that already
carry a session ID keep resuming exactly as before, and no-replay and
uncertain-turn behavior is unchanged. Run Python through `cmd.exe` redirection
with unbuffered output so stdout and stderr append to the log without
PowerShell turning stderr into a fatal error, and propagate the exit code.

</frozen-after-approval>

## Implementation Notes

- Live evidence (2026-09-16): the iOS pilot grant's session
  `20260916_165559_53a9b9` was absent from the `amanda` profile `state.db`; the
  Standard pilot log recorded `session.reclaimed` with `ws_orphan_reap`; a
  replayed `conversation.open` then returned `unavailable` /
  `request_rejected`. On CaticornQueen, the old runner pattern around a Python
  one-liner that wrote one stderr line exited 1 with a 0-byte log; the new
  `cmd.exe` pattern appended stdout and stderr, survived, and returned the
  child's exit code (path containing a space included).
- `src/hermes_home/bridge/standard.py`: `_unpersisted_session_id` holds a new
  Session's durable ID. `_open` persists only when the grant already carries a
  session ID; `_reconnect` likewise refreshes only an already-bound grant and
  otherwise keeps the ID pending. `_persist_session_after_accepted_turn` runs
  after Standard accepts a prompt and the binding is still current; a persistor
  failure keeps the accepted turn and leaves the ID pending for the next
  accepted turn. The pending ID is cleared with the rest of the binding in
  `_invalidate_binding` and `close`.
- `deploy/windows/run.ps1`: `PYTHONUNBUFFERED=1` and `Start-Process` of
  `cmd.exe /d /s /c "<python> -m hermes_home.runtime >> <log> 2>&1"` with
  `-Wait -PassThru`, exiting with the child's exit code.
- Tests: the old open-time persistence test became four tests (no binding on
  open, binding after the first accepted prompt, accepted turn survives a
  persistor failure, an already-bound grant still resumes and refreshes);
  the Windows artifact test asserts the runner no longer lets PowerShell own
  native redirection.
- Verification: focused 96 passed; full 340 passed; `ruff check` and
  `ruff format --check` clean.
- Operational follow-ups outside this code change: clear stale `session_id`
  values already written to pilot grants (iPhone and Android), then redeploy.
  The scheduled task still does not restart after a non-zero exit.

## Review Triage Log

- Blind Hunter layer skipped (no subagent authorized); inline self-review of
  the diff was performed instead.
- low (rejected) — if the first prompt's delivery is uncertain, its Session ID
  stays unpersisted, so a new endpoint connection starts a fresh Session rather
  than resuming one Hermes may have stored. Persisting an unconfirmed ID would
  reintroduce the permanent `request_rejected` failure; in-connection reconnect
  still resumes via the in-memory ID.
- low (rejected) — a persistor failure after an accepted turn is not logged;
  the module has no logger, the turn is unaffected, and the next accepted turn
  retries.
- maybe-false (deferred) — grants that already hold a reaped, never-stored
  session ID still fail until cleared; Home cannot distinguish that from a
  genuinely stored Session that Standard rejects. Settled by a Standard error
  code that distinguishes "not found".
