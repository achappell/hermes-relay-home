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
- One new smoke prompt was sent once. Home diagnostics show it was accepted,
  completed by Hermes, and its audio completed. The app instead reported
  `protocol_error`, then `transport_timeout`, and marked the turn unconfirmed.
  This proves server-side completion but not delivery to the app. The prompt
  was not resent.
- After the iOS open-response decoder fix, the rebuilt signed-in Xcode app
  reconnected and reached Home Ready. The old turn remains visibly unconfirmed.
  Home explicitly returned `unresolved_turn: false`, so the app offered
  “Continue without resending.” Selecting it retained the earlier user prompt,
  cleared only its stale recovery state, and preserved the composer draft. The
  old prompt was not resent.
- A distinct composer prompt was then sent once. For correlation
  `corr-cffc857d828b40c891414f7fff3db66b`, Home recorded endpoint start, Hermes
  acceptance, Hermes terminal completion, PCM chunks accepted, and audio
  completion between 1:33:46 and 1:33:50 Central. The app showed some streaming,
  then reported `transport_timeout` and audio unavailable after about 30
  seconds. The prompt remains unconfirmed and was not resent. Home's successful
  socket sends prove server-side delivery attempts, not client receipt or
  decoding. No prompt text, response text, or audio was added to diagnostics.
- Code review found a protocol mismatch: Home forwards safe Standard event
  payload fields, including future additions, while the Apple client rejected
  unknown payload keys. The iOS follow-up now ignores unconsumed Standard
  payload fields while validating fields it reads; Home envelope keys remain
  strict. A wire-level regression covers `message.complete` with extra
  metadata. The change postdates the live smoke and has not been validated
  against a new live turn.
- After that change, the full iPhone 17 Pro simulator suite passed (437 tests,
  zero failures), and the macOS Xcode product built and passed strict code
  signature verification.

The Home reconnect fix is deployed and the parked-turn recovery path is
validated. End-to-end reply delivery and the post-response activity indicator
remain unconfirmed: the latest live turn timed out in the app despite Home
recording terminal and audio completion. A new live turn is required to verify
the Apple decoder follow-up and final UI state. The existing prompts remain
protected as unconfirmed; no prompt was resent.
