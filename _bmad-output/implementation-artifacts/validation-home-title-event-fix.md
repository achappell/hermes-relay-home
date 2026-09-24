---
status: passed-local
validated: 2026-09-23
baseline_commit: d2447f684a143817f7b5688b597c11aa053bb672
---

# Home title event and upstream-loss repair

The personal-client live trial accepted a turn but never displayed its completed response. A read-only inspection found Standard's title event uses a runtime session ID in the envelope and a durable session ID in its payload. Home treated that valid pair as conflicting identities, stopped its reader, and closed the upstream connection. Its endpoint event pump marked itself unavailable without closing the downstream socket, leaving the client waiting.

## Changes

- Accept the distinct durable payload identity only for `session.title`, requiring its runtime envelope identity. Other event types retain conflicting-identity rejection.
- On genuine upstream transport failure, escaping timeout, or unavailable runtime, close the downstream connection with WebSocket code 1011. Preserve the bridge and uncertain turn for authenticated reconnect; do not cancel or replay the turn.
- Keep the existing quiet idle-timeout behavior separate from transport failure.

## Evidence

`uv run --python 3.14 --locked --extra dev pytest -q tests/test_standard_bridge.py tests/test_bridge_endpoint.py`: 216 passed. The new regressions cover title events followed by a complete streamed reply, rejection of title events without a runtime envelope, downstream closure with preserved adoptable uncertain state and no prompt replay, while the existing real HomeBridge idle-poll regression proves the healthy gateway remains ready after a quiet poll. The existing conflicting turn-event identity test still passes.

`uv run --python 3.14 --locked --extra dev pytest -q`: 683 passed in 8.11s before review additions; final complete suite after review additions: 684 passed in 7.46s.

`uvx ruff check src tests`: passed. `uvx ruff format --check src tests`: 68 files already formatted. `git diff --check`: passed.

No Standard Hermes changes, deployment, service restart, or commits were made. Live validation of this candidate remains pending.

## Deployment boundary

The Windows installer can clear omitted Standard connection settings and also updates Prometheus. A narrowly scoped candidate deployment should preserve existing machine settings, credentials, and database; back up the installed Home package; install a verified wheel without changing dependencies; restart only the Home scheduled task; and verify health plus the personal-client conversation path. Retain the previous package for rollback. Do not use an installer invocation that omits the existing settings.

## Review triage

- Foreign-runtime title isolation: tolerable verification gap, now closed. `test_title_durable_identity_does_not_discard_remaining_turn_events` first injects a foreign runtime envelope with the current durable payload ID and proves it is filtered; the accepted title and following completion remain correctly attributed.
- Accepted title privacy projection: tolerable verification gap, now closed. The same test asserts the public title payload contains only the title and that its durable ID is absent from the serialized endpoint event. Existing conflicting turn-identity rejection is unchanged.
- Combined disconnect/parking uncertainty: tolerable verification gap, now closed. `test_real_bridge_upstream_loss_parks_endpoint_and_retains_original_uncertain_turn` runs a real HomeBridge and BridgeEndpoint with a failing synthetic gateway through `run(park_on_disconnect=True)`. It verifies close code 1011, a parked recoverable endpoint, real bridge state `turn_uncertain`, authenticated adoption exposing the original uncertain turn ID, and exactly one upstream prompt. `test_bridge_reconnects_by_resuming_without_replaying_uncertain_prompt` separately verifies upstream resume; `test_live_server_requires_reconnect_and_adopts_the_parked_turn` covers the live server's parking path.

Expanded focused check after review: `uv run --python 3.14 --locked --extra dev pytest -q tests/test_standard_bridge.py tests/test_bridge_endpoint.py tests/test_bridge_server.py`: **226 passed in 2.86s**. These additions required no production-code changes.
