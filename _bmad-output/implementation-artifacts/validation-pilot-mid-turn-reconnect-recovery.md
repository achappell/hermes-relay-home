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

The end-to-end turn is not yet validated. The Home side completed it, but the
signed-in app did not render the response. The iOS follow-up currently covers
the Home open response shape seen during an active turn; it must pass its
focused and full test suites and be exercised before claiming the spinner or
delivery issue is fixed.
