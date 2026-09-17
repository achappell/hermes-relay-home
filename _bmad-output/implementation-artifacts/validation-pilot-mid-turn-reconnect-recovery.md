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
  A later, different composer submission was blocked by the app's recovery
  guard before it was sent; the original prompt was not resent.
- Inspection of the Home terminal contract found that Standard's successful
  `message.complete` event carried the completed status and final text, while
  the iOS normalizer did not emit `turnComplete`. The iOS follow-up maps that
  status into a terminal event while preserving nonterminal `message.complete`
  events for continued streaming. Its focused regression, all 431 iOS tests,
  and the macOS build pass.

The single live turn is not end-to-end confirmed: Home recorded acceptance,
terminal completion, and audio completion, but the app never rendered that
reply. The updated app now reconnects cleanly, and the missing terminal mapping
has a regression test. A fresh live turn could not be tested because the app
keeps the earlier unconfirmed turn protected from replacement or resubmission.
The post-response spinner behavior still needs one clean live turn to verify.
