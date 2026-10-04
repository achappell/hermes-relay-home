---
story: HOME-NW-18
status: passed-local
validated: 2026-10-03
baseline_commit: b77c830d0c282cee44cdcbbec5008ab754392e01
implementation_commit: d043192eaa4022689f59d78b3f3dca164dbb8dfc
---

# HOME-NW-18 validation record

## Verification boundary

Home mints a `claim_ref` for each client claim, lists a device's active
client claims (`GET /api/v1/client-claims`) and closes an explicit set of them
(`POST /api/v1/client-claims/close`). Deterministic tests cover the pilot
claim-leak scenario, active-state mapping, wall-clock timestamps, device
isolation, `not_open` answers, request validation, proxied requests,
configuration degradation, absence of Standard calls, credential denials,
identifier safety in logs/diagnostics, metrics, sticky close reasons,
concurrent close/create/open/reconnect, and NW-17 to NW-18 migration.

Bridge coverage includes mid-turn client close through a real `HomeBridge` and
`BridgeEndpoint` using fake Standard sockets (interrupt, close code 1000,
`stale_conversation`), close races during open/reconnect, the per-handle
revocation guard, detached-marker activity precedence, parking-lot mark/take/
expiry synchronization, and production `create_bridge_server` claim-store
wiring. API coverage verifies a closed session is listed inactive and can be
resumed using its durable session reference.

This validates the Home slice against fake Standard sockets and the production
SQLite claim store. A disposable runtime also exercised Home HTTP on loopback
and the real `create_bridge_server` WebSocket handshake/request-response path
with an intentionally invalid method. It does not claim a gateway-backed claim
close over real WebSockets, live Standard Hermes, Tailscale Serve, iOS, or
deployment. No sibling repository was changed.

## Observed checks

| Check | Exact command | Result |
| --- | --- | --- |
| Python runtime | `uv run --python 3.14 --locked --extra dev python --version` | `Python 3.14.8` |
| Focused HOME-NW-18 and adjacent bridge/claim suites | `uv run --python 3.14 --locked --extra dev pytest -q tests/test_client_claim_store.py tests/test_client_claim_list_close_api.py tests/test_client_claim_close_bridge.py tests/test_bridge_server.py tests/test_runtime.py tests/test_client_claims_api.py tests/test_standard_bridge.py tests/test_bridge_endpoint.py` | `382 passed in 11.43s` |
| Runtime test file after removing forwarding-only assertion | `uv run --python 3.14 --locked --extra dev pytest -q tests/test_runtime.py` | `21 passed in 0.86s` |
| Full Home test suite | `uv run --python 3.14 --locked --extra dev pytest -q` | `818 passed, 2 warnings in 15.91s` |
| Ruff lint | `uvx ruff check src tests` | `All checks passed!` |
| Ruff format | `uvx ruff format --check src tests` | `73 files already formatted` |
| Lockfile check | `uv lock --check` | `Resolved 17 packages in 9ms` |
| Disposable loopback runtime smoke | One-shot inline Python with `uv run --python 3.14 --locked --extra dev`; no script file persisted | Actual Home HTTP runtime on loopback; temporary SQLite/credential state removed. Two devices enrolled and authenticated; each created one claim. Device 1 listed only its own ref; close returned `closed`; subsequent list was empty; repeat close returned `not_open`; Device 2 still listed its own claim. A real local `create_bridge_server` WebSocket handshake and message exchange returned JSON-RPC `-32601 invalid_request` for the intentionally invalid method. |
| Whitespace/diff | `git diff --check` | Passed; no output |

## Deviations from the spec text

- The detached marker is set and cleared by the bridge server's parking lot
  (`park`, `take`, park expiry), not by `BridgeEndpoint.detach`/`adopt`; parking
  is where "parked" is defined, and the endpoint needs no store reference.
  `mark_detached` takes only the handle (handles are unique), and parking-lot
  notifications are serialized with entry insertion/removal.
- `create_bridge_server` takes an optional `claim_store`; it registers the
  parking lot's `evict` as a store close listener. Every notifying close
  (client close, device, grant or Profile revocation) evicts a parked endpoint.
- Only `client_closed` is terminal in the endpoint. Other stale authorization
  failures, including device/grant/Profile revocation and generation or
  configuration changes, retain the existing upstream-failure/reconnect path.
- `mark_open` refusing an inactive or expired claim yields `stale_conversation`
  (open and reconnect), matching the close-versus-open row.
- `create_client_claim` returns `(handle, claim_ref)`.
- Client-route diagnostic failure codes map onto the existing allowlist
  (`client_claim_unavailable` → `forbidden`, `claim_limit` and other denials →
  `claim_denied`, configuration migration → `conflict`); `client_sessions` is
  also a safe route ID.
- Close waits for serialized handlers, so it may take longer than 15 seconds;
  after a client timeout the client must re-list rather than assume the close
  did not happen.

## Review findings disposition

- **D1 — terminal close semantics:** Implemented only for `client_closed`;
  other stale authorization failures retain the pre-existing reconnect/upstream
  behavior. Parameterized bridge tests cover device/grant/Profile revocation and
  generation/configuration changes. Remaining gap: local WebSocket smoke used an
  intentionally invalid method, not a live claim close; no live Standard Hermes.
- **D2 — close timeout guidance:** Spec and v1 contract now explain serialized
  notification handlers may exceed 15 seconds and clients must re-list after a
  timeout. Household-scale latency was not measured.
- **P1 — parking marker race:** Marker set/clear callbacks are serialized with
  parking entry publish/take/expiry. Deterministic gated race tests cover park,
  take, and expiry. No cross-process marker behavior is claimed; markers are
  intentionally process-local.
- **P2 — runtime/parking integration:** A behavioral test closes a parked claim
  through the store and confirms the parking entry is evicted. The disposable
  runtime smoke also exercised the actual local WebSocket listener. A real
  gateway-backed conversation was not opened.
- **P3 — close/reopen races:** Tests cover close against open/reconnect and
  `mark_open` on inactive/expired rows. These use controlled fake upstream
  sockets, not live Standard.
- **P4 — diagnostics contracts:** Safe `client_sessions` route ID and diagnostic
  aliases, including configuration-migration conflict, are pinned by API tests.
- **P5 — identifier leakage:** Captured logs/diagnostics are checked for private
  refs/handles/session/grant identifiers in live-close, parked-close, and
  interrupt-failure cases. This covers observed test output only.
- **P6 — edge-case coverage:** Tests cover detached-marker precedence for all
  active activities, Room wake/touch exclusion, API close/list/resume behavior,
  migration degradation, store failures, and repeated migration. Runtime smoke
  confirms end-to-end authenticated list/close/isolation behavior for two
  temporary devices.

Remaining verification boundary: no live household traffic, Standard Hermes,
Tailscale Serve, iOS client, deployment, or host-sleep behavior was exercised.

## Not verified

Live Standard Hermes interrupt behavior, Tailscale Serve routing, iOS
consumption, deployment, host-sleep behavior, and gateway-backed claim close
were not exercised. The local WebSocket smoke verified the real loopback server
handshake and request/response path with a deliberately invalid method only.
