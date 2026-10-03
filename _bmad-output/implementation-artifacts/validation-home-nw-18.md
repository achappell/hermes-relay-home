---
story: HOME-NW-18
status: passed-local
validated: 2026-10-03
baseline_commit: 07de094
implementation_commit: 59fc9ee
---

# HOME-NW-18 validation record

## Verification boundary

Home now mints a `claim_ref` for each client claim, lists a device's active
client claims (`GET /api/v1/client-claims`) and closes an explicit set of them
(`POST /api/v1/client-claims/close`). Deterministic tests cover the pilot
claim-leak scenario, the full activity-to-state mapping including parked
endpoints, wall-clock `created_at`/`opened_at`, device isolation, `not_open`
answers, body validation, proxied requests, configuration degradation, the
absence of Standard calls, credential denials, identifier safety in logs,
metrics and diagnostics, the sticky first close reason, concurrent close and
create, the NW-17 to NW-18 migration, a live mid-turn close through a real
`HomeBridge` and `BridgeEndpoint` (session.interrupt, close code 1000,
`stale_conversation`), a close racing an in-flight open, the per-handle
revocation guard, and parking-lot marking, expiry and eviction.

This validates the Home slice against fake Standard sockets, a fake socket
connection and the production SQLite claim store. It does not claim live
Standard Hermes, a real WebSocket server round trip through
`create_bridge_server`, Tailscale Serve, iOS, or deployment. No sibling
repository was changed.

## Observed checks

| Check | Exact command | Result |
| --- | --- | --- |
| Python runtime | `uv run --python 3.14 --locked --extra dev python --version` | `Python 3.14.8` |
| Focused HOME-NW-18 and adjacent bridge/claim suites | `uv run --python 3.14 --locked --extra dev pytest -q tests/test_client_claim_store.py tests/test_client_claim_list_close_api.py tests/test_client_claim_close_bridge.py tests/test_client_claims_api.py tests/test_standard_bridge.py tests/test_bridge_endpoint.py tests/test_bridge_server.py` | `341 passed in 8.44s` |
| New HOME-NW-18 tests, repeated 8 times for thread races | `pytest -q tests/test_client_claim_close_bridge.py tests/test_client_claim_store.py tests/test_client_claim_list_close_api.py` | `52 passed` every run |
| Full Home test suite | `uv run --python 3.14 --locked --extra dev pytest -q` | `798 passed, 2 warnings in 13.81s` (758 before; warnings pre-existing) |
| Ruff lint | `uvx ruff check src tests` | `All checks passed!` |
| Ruff format | `uvx ruff format --check src tests` | `73 files already formatted` |
| Lockfile check | `uv lock --check` | Passed |
| Whitespace/diff | `git diff --check` | Passed; no output |

## Deviations from the spec text

- The detached marker is set and cleared by the bridge server's parking lot
  (`park`, `take`, park expiry), not by `BridgeEndpoint.detach`/`adopt`; parking
  is where "parked" is defined, and the endpoint needs no store reference.
  `mark_detached` takes only the handle (handles are unique).
- `create_bridge_server` takes an optional `claim_store`; it registers the
  parking lot's `evict` as a store close listener. Every notifying close
  (client close, device, grant or Profile revocation) evicts a parked endpoint.
- The endpoint treats any `BridgeAuthorizationError("stale_conversation")` from
  `next_event` as terminal, not only `BridgeClaimClosed`, so a close detected
  by claim revalidation ends the same way.
- `mark_open` refusing an inactive or expired claim now yields
  `stale_conversation` (open and reconnect) instead of
  `authorization_unavailable`, matching the spec's close-versus-open row.
- `create_client_claim` returns `(handle, claim_ref)`.
- Client-route diagnostic failure codes are mapped onto the existing allowlist
  (`client_claim_unavailable` → `forbidden`, `claim_limit` and other denials →
  `claim_denied`, and so on); `client_sessions` is also a safe route ID.

## Not verified

Live Standard Hermes interrupt behaviour, the real WebSocket server path
(parking via `create_bridge_server` with real sockets), Tailscale Serve
routing of the new paths, iOS consumption, and wall-clock behaviour across host
sleep.
