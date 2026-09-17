# Pilot mid-turn reconnect recovery validation

Validated 2026-09-17 in the
`fix/pilot-session-persist-and-runner-log` worktree.

| Check | Result |
|---|---|
| Parked endpoint adoption | A matching authenticated reconnect reused the original bridge and accepted turn; no Standard reconnect, Session resume, or prompt resubmission occurred. |
| Plain open during grace period | Returned `unavailable` with `reconnect_required`. |
| Buffered text ordering | The reconnect response arrived before buffered Standard text events. |
| Audio/text independence | Audio ended while the accepted turn remained active for later text and its terminal event. |
| Focused tests | `141 passed in 2.52s` on Python 3.14.7 |
| Full tests | `364 passed` on Python 3.14.7 |
| Static checks | Ruff check passed on changed source and tests; `ruff format --check` passed on the six changed Python files; `git diff --check` passed. |

## Windows pilot deployment and signed-in macOS app check (2026-09-17)

- Built pilot wheel SHA-256: `b425e23b9d3725ba5853b013c774a5994ba1f89c01e0f06acfaa1fb93fb0c5f6`.
- Installed that exact wheel on CaticornQueen and verified the installed hashes
  of `bridge_server.py`, `endpoint.py`, and `standard.py` against this worktree.
- The existing `Hermes Home` scheduled task is running. `/metrics` returned
  HTTP 200 and an unauthenticated bridge request returned HTTP 401. Prometheus
  was not changed.
- The signed-in Xcode product reconnected as Amanda's MacBook Air and showed
  Home Ready after the Home service restart. Its existing unsent composer draft
  was preserved through relaunch.
- Two earlier smoke attempts timed out in the app even though Home recorded
  successful terminal and audio events. Both used the signed-in app process
  launched at 1:33, before the decoder fix was committed. The bundle on disk had
  been rebuilt, but that old process remained open with stale code; those runs
  did not test the decoder fix. Neither prompt was resent.
- Code review found a protocol mismatch: Home forwards safe Standard event
  payload fields, including future additions, while the Apple client rejected
  unknown payload keys. The iOS follow-up now ignores unconsumed Standard
  payload fields while validating fields it reads; Home envelope keys remain
  strict. A wire-level regression covers `message.complete` with extra
  metadata.
- After that change, the full iPhone 17 Pro simulator suite passed (437 tests,
  zero failures), and the macOS Xcode product built and passed strict code
  signature verification.
- Rebuilt the exact signed Xcode product at 3:02 Central and relaunched it. The
  app reconnected as Amanda's MacBook Air, reached Home Ready, and recovered
  the earlier turn as explicitly unresolved=false. Selecting “Continue without
  resending” cleared only the stale recovery state; the old prompt was not
  resent.
- Sent a distinct smoke prompt once at 3:03:15 Central. For correlation
  `corr-814d90a2e7c34d4ca5ed69a6c51c28d2`, Home recorded endpoint start and
  acceptance, Hermes text completion at 3:03:16, accepted audio chunks, and
  audio completion at 3:03:23. The signed-in app displayed the exact
  `FRESH_PILOT_OK` reply, ended playback, returned to “Tap to record” / Ready,
  and showed Home Ready and Hermes Complete. No timeout or unconfirmed-turn
  banner remained. The prompt was not resent, and diagnostics contain no prompt
  text, response text, or audio.

The Home reconnect fix is deployed, and the signed-in macOS end-to-end path is
validated through reply delivery and return to Ready after voice playback.
Earlier unconfirmed prompts remain protected; none was resent.
