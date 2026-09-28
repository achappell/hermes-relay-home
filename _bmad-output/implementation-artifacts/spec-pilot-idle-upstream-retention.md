---
title: 'F1 — retain healthy idle upstreams through reconnect grace'
type: 'bugfix'
created: '2026-09-27'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context:
  - _bmad-output/implementation-artifacts/spec-pilot-dead-upstream-reconnect-loop.md
---

<frozen-after-approval reason="user requested F1 from the parent spec">

## Intent

**Problem:** Home closes a healthy idle upstream when its client backgrounds because recoverable endpoint state requires an active turn or buffered events. Endpoint parking also defaults to 90 seconds while the configured client claim grace defaults to 120 seconds; a return after 106 seconds therefore loses the retained upstream.

**Approach:** Retain an authorized healthy endpoint across client detach even when idle, using the configured reconnect grace for endpoint parking. Reauthorize before adoption; do not replay prompts or renew the original deadline on rejected adoption. Keep the current keepalive defaults because no collected evidence establishes a keepalive-setting defect. Given an idle connected conversation, when the client disconnects and reconnects within grace, then it adopts the healthy upstream and a subsequent prompt completes. Given grace expires, then the parked endpoint is closed. Given a nondefault runtime grace, then claim and endpoint retention use that same value.

</frozen-after-approval>

## Implementation Notes

- Investigation: Home websockets 17.1 and remote proxy websockets 15.0.1 both use 20-second interval/timeout defaults. Home keepalive has its own thread; proxy bidirectional reads have no idle timeout. Remote Standard disables server pings for loopback. Proxy swallows expected close exceptions without codes; no historical first-close attribution is established. No remote configuration changes or live client reproduction performed.
- Scope: `endpoint.py` recoverable-state predicate, `runtime.py` listener grace wiring, listener/runtime tests. No new public API, data migration, deployment or irreversible change. F2 rebuilding remains a separate slice.

- Implemented healthy idle retention for bridges that support reauthorization and wired configured runtime grace into the listener. Failed adoption preserves the original readiness and parking deadline. Listener shutdown drains parked endpoints and cancels timers, including late parking during shutdown.
- Validation: Python 3.14 focused endpoint/listener/runtime suite — 136 passed; Ruff check and format check passed (68 files). Live WebSocket tests cover idle reuse, wrong-credential rejection followed by valid adoption, a completed follow-up prompt, retention after completion, grace expiry, and shutdown cleanup. Full two-minute deployed-device reproduction remains pending.

## Review Triage Log

- High, patched: rejected idle adoption cleared readiness and destroyed retention. Preserve the original readiness on unavailable adopted responses; test wrong then valid credentials.
- Medium, patched: shutdown left newly retained idle upstreams alive. Drain the parking lot, cancel timers and reject late parking during shutdown.
- Medium, patched: test could pass immediate teardown after prompt completion and used a narrow timing margin. Assert continued retention after completion and separately verify expiry/shutdown with wider timing allowance.
- Reviewer checked all three corrections and found no remaining issues in them. No review findings deferred.
