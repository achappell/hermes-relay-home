---
title: 'Pilot bridge — retire or rebuild a conversation whose Hermes link has died'
type: 'bugfix'
created: '2026-09-27'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context:
  - _bmad-output/implementation-artifacts/spec-pilot-mid-turn-reconnect-recovery.md
---

## Intent

**Problem:** When a conversation's upstream Standard connection dies, Home keeps the dead connection attached to the conversation. `BridgeEndpoint._event_loop` catches the upstream failure, marks the endpoint unavailable, closes only the client socket with 1011, and continues polling the same `HomeBridge`. The endpoint is then parked under the mid-turn reconnect rules, which reuse the existing bridge without reconnecting the Standard gateway. Every `conversation.reconnect` for that handle reauthorizes, answers `ready`, and is closed milliseconds later when the next `bridge.next_event()` raises again. A failed adoption reparks the endpoint, so the loop repeats until the 120-second client reconnect grace expires. The device cannot recover without starting a new conversation.

**Approach:** Treat a dead upstream as terminal for that bridge instance. On an upstream transport, timeout, or `RuntimeError` failure outside `_conversation_closing`, the endpoint must stop offering the dead bridge for adoption. Preferred: on the next authorized `conversation.reconnect`, rebuild the bridge by reconnecting to the Standard gateway and resuming the durable Session bound to the claim, reporting the interrupted turn as unresolved (`delivery: uncertain`). If rebuilding fails, or as the first slice if rebuilding is out of scope, close the endpoint and claim immediately with a typed, non-retryable result, so the client starts a fresh conversation instead of looping.

## Evidence — live pilot, 2026-09-27

Device `e6d1774…` (iPad14,5, iOS 26.6.1, app 0.5.0), shared Profile `spark`, claim `RcK0VCVQ…`, Session `20260927_193249_71f12e`. Times are UTC.

| Time | Observation |
| --- | --- |
| 00:32:49 | Claim created; `conversation.open` ready. |
| 00:32:53, 00:33:58, 00:34:54 | Three turns accepted and completed; the 00:33:58 turn streamed about 2 MB of response audio, finishing at 00:34:54. |
| 00:35:07 | The client's `prompt.submit` fails after 1015 ms with `transport_unavailable`, `uncertain=true`. No Home turn event is recorded for it. |
| 00:35:21–00:36:01 | Ten reconnects: each gets `open result=ready`, then the transport is lost 10–25 ms later (`reconnect result=disconnected code=transport_unavailable`). |
| Home log, same window | Repeated `Home upstream unavailable: error=BridgeTransportError cause=RuntimeError detail=unclassified`, plus `keepalive ping failed … sent 1011 (internal error) keepalive ping timeout`. |
| by about 00:42 | The claim closes with `client_disconnected` after the grace period. A new conversation on the same device then works normally. |

Other devices' conversations on the same Standard gateway completed turns at 00:35:12, 00:35:30 and 00:37:12, so the gateway itself stayed healthy. Only this conversation's upstream connection was dead.

**Second occurrence, same device, no failed turn.** New claim `-BzUZpe4…` opened at 00:45:50 and completed two turns (00:45:53, 00:46:53). The app went to the background at 00:53:08 (client transport lost, as expected) and returned at 00:54:54. The reconnect answered `ready`, lost the transport 11 ms later and failed with `transport_unavailable`. At the same second the Home log added another `Home upstream unavailable … cause=RuntimeError` line. The claim stayed `active`/`disconnected` with its grace deadline pending. So the upstream connection also dies while a conversation sits idle and disconnected for about two minutes, not only after a failed mid-turn send. An ordinary app switch is enough to strand a conversation.

Not established: which side's keepalive expired first (the Home log has no timestamps), and whether the 2 MB audio stream starved the sync keepalive. The fix must not depend on the answer.

## Safety and lifecycle

- An endpoint whose bridge has raised an upstream failure is never adopted onto that same bridge instance again.
- A reconnect answered `ready` must be backed by an upstream connection that can deliver events. No `ready` followed by an immediate 1011.
- Rebuilding keeps all adoption checks from the mid-turn recovery story: device credential, active grant, device, handle, Profile, and durable Session binding.
- A rebuild resumes the Session bound to the claim; it never creates a second Session for the handle and never resubmits the interrupted prompt.
- If the Session cannot be resumed, the claim closes with a durable `close_reason` (for example `upstream_lost`) and the reconnect gets a typed error the client treats as "start a new conversation". No retry loop.
- No busy loop: after an upstream failure the event pump stops polling the dead bridge.
- Diagnostics stay content-free. Log the claim handle prefix and failure class, and add a timestamp to the upstream-unavailable line so future timelines can be aligned.

## Acceptance

- **Fake-gateway test, dead upstream mid-conversation:** a fake Standard connection raises a transport failure after one completed turn. A subsequent authorized `conversation.reconnect` either returns ready on a freshly connected fake gateway with the same Session ID, or returns the typed terminal error with the claim closed. It never returns ready and then closes 1011.
- **Repeated reconnects:** five reconnects against a dead upstream produce at most one rebuild attempt per reconnect and no repark of a bridge known to be dead.
- **Healthy parking unchanged:** the existing mid-turn recovery tests (client socket drops, upstream healthy) still pass unchanged.
- **Client contract:** the terminal error code is one the iOS and Android clients already map to a fresh conversation, or a new code documented and handed to IOS-*/Android story owners. Record the handoff in the product hub; don't edit client repositories from this story.
- **Pilot check:** after deploy, kill the Home→Standard relay socket for one live conversation. The device either resumes or is told to start a new conversation within one reconnect, and never loops.

## Code pointers

- `src/hermes_home/bridge/endpoint.py` `_event_loop`: the `BridgeTransportError`, timeout and `RuntimeError` branches `continue` with the same bridge.
- `src/hermes_home/bridge/endpoint.py` `adopt` and `handle_message`: adoption is acknowledged `ready` without probing upstream health.
- `src/hermes_home/api/bridge_server.py` `_EndpointParkingLot.park`: reparks after failed adoption.
- `src/hermes_home/bridge/standard.py`: the `standard gateway is not connected` `RuntimeError` raises are the likely inner cause.

## Implementation Notes

- Selected the explicitly permitted terminal first slice. Upstream transport/timeouts/runtime failures stop the event pump, revoke readiness, close the durable claim with `upstream_lost`, and dispose of the bridge. Automatic upstream rebuilding is not part of this slice. The stored Session is retained for deliberate resume; no prompt is resent.
- `src/hermes_home/bridge/endpoint.py`: reject adoption and readiness after retirement starts, exclude stopped/closed endpoints from recoverable state, and log UTC timestamp, handle prefix and safe failure classes. Expected failures during deliberate conversation closure remain ignored.
- `src/hermes_home/bridge/standard.py`: retire the claim through the existing durable closer before closing transport. `src/hermes_home/api/bridge_server.py`: refuse parking an unrecoverable endpoint.
- Existing wire contract: subsequent authenticated reconnect resolves the closed claim as `unavailable` / `stale_conversation`. Read-only client inspection confirms Android excludes `StaleConversation` from `canAttemptConnection` in `MainActivity.kt`; iOS `ConversationStore.swift` exits Home reconnect on `.unavailable`. No new wire code or client repository edits.
- Regression coverage in `tests/test_bridge_endpoint.py` and `tests/test_standard_bridge.py`: four failure classes, pump termination, five rejected adoptions, real HomeBridge with SQLite claim closure after interrupted/completed turns, preserved stored Session, five typed terminal reconnects, and no prompt replay. Existing healthy parking tests remain unchanged.
- Pilot deployment/socket-kill acceptance remains pending; local tests do not establish live-device acceptance.

- Idle-case investigation: an idle connection with no buffered events closes its upstream when the client backgrounds; foreground reconnect creates a fresh bridge. A parked endpoint can become idle after completing its turn while retaining buffered events. Both paths now have live WebSocket tests. Expected shutdown errors after `_stop` are ignored rather than logged as upstream failures; the pilot log alone cannot distinguish these paths.
- `src/hermes_home/bridge/production.py` blocks a failed-upstream claim in memory if its durable close write fails, retries the write on lookup, and never readmits it. Existing startup behavior closes all active claims after process restart. SQLite abort-trigger tests prove rejection during write failure and persistence after recovery.
- Client evidence: iOS revision `ab1c3008c33c91980bac8389fdc811af8514a118`, `HermesRelay/ViewModels/ConversationStore.swift:2302`; Android revision `f4e0b6609c1dfe1dd6598b8d0ff15d82c8e1ef9a`, `app/src/main/java/com/achappell/hermesrelay/MainActivity.kt:224`. Product-hub handoff recorded under “Handoff — dead upstream and idle reconnect (2026-09-27)” in the canonical private Hermes Home hub. No external messages sent.

## Follow-ups — keep the conversation connected (added 2026-09-27)

This slice makes a dead upstream fail fast with `stale_conversation`, so the client asks the user to start a new conversation. The two follow-ups below keep the user in their conversation without that prompt. Each should be its own slice, done in this order.

### F1 — Stop the idle upstream from dying

**Problem:** In the second occurrence, the upstream connection died while the conversation sat idle and client-disconnected for about 106 seconds (00:53:08–00:54:54 UTC). The Home→Standard connection (Home → Tailscale Serve `media-server:8443` → pilot proxy `127.0.0.1:9121` → `hermes serve` `127.0.0.1:9120`) shouldn't depend on the client socket, so an app switch should never kill it. Something in that chain drops or times out an idle or backgrounded connection.

**Investigate:**
- the Home websockets client keepalive settings (`ping_interval`/`ping_timeout`), and whether the sync client's keepalive thread can be starved while the event pump or audio reader holds the connection
- idle timeouts or ping handling in the pilot proxy (`~/.hermes/hermes-home-standard-pilot/proxy.py` on media-server) and in `hermes serve`
- what happens to the upstream when the client detaches without an accepted turn: whether Home deliberately closes or stops servicing it while waiting for the 120-second grace period

**Evidence to collect first:** reproduce by backgrounding a client for two minutes. Line up the new timestamped `Home upstream unavailable` line with `proxy.log` and `standard.log` to see which hop closed first, and with what code.

**Acceptance:** a conversation left idle and client-disconnected for the full reconnect grace period keeps a healthy upstream. A reconnect within grace returns `ready` and the next `prompt.submit` completes.

### F2 — Rebuild the upstream on reconnect instead of retiring the conversation

**Problem:** Even with F1, networks, sleeps and gateway restarts will still kill some upstream connections. Today every such loss ends the conversation.

**Approach:** the preferred path from Intent. On an authorized `conversation.reconnect` whose upstream has died, reconnect to the Standard gateway and resume the durable Session bound to the claim, instead of closing the claim with `upstream_lost`. Report an interrupted turn as unresolved (`delivery: uncertain`); never resend it. Fall back to this slice's `stale_conversation` only when the Session cannot be resumed.

**Constraints:** keep every adoption check from the mid-turn recovery story; at most one rebuild attempt per reconnect; no second Session for a handle; bounded retry with no loop. Response audio from the lost turn is not replayed.

**Acceptance:** with a fake gateway, kill the upstream after a completed turn. The next reconnect returns `ready` with the same Session ID, and a follow-up prompt shows Hermes still has the earlier context. With the Session unresumable, the reconnect returns `stale_conversation`, as in this slice. Live check on the pilot: kill the relay socket for one conversation, and the device continues the same conversation after one reconnect.

**Client impact:** none expected. A successful rebuild looks like an ordinary `ready` reconnect to iOS and Android.

## Review Triage Log

- High, patched: concurrent endpoint cleanup could clear claim identity before retirement. `_retiring_upstream` prevents cleanup from overtaking the close write; a blocking-retirement regression proves ordering.
- High, patched: failed persistence could allow the durable claim to resolve again after losing unresolved-turn state. Production claim resolution now rejects quarantined failed-upstream handles and retries persistence; an injected SQLite abort proves five reconnects stay terminal and eventual recovery records `upstream_lost` without deleting the Session.
- Medium, patched: normal WebSocket close code could suppress client recovery. Upstream failure retains 1011; the real-bridge regression asserts it.
- Medium, patched: initial tests bypassed server parking. Live WebSocket tests now traverse parking, failed adoption, replacement and five terminal reconnects for both active and idle parked conversations.
- Low, patched: client evidence lacked revision/source and product-hub handoff. Exact inspected revisions and source locations are recorded above, along with the completed handoff and pending pilot acceptance.

## Validation

- Python 3.14: `uv run --python 3.14 pytest -q tests/test_bridge_endpoint.py tests/test_bridge_server.py tests/test_standard_bridge.py tests/test_production_bridge.py` — 272 passed.
- `uvx ruff check src tests` — passed. `uvx ruff format --check src tests` — 68 files already formatted. Ruff is not installed in the project environment; the tool runner supplied it without changing project dependencies.
- Deployment and live iOS/Android socket-kill acceptance remain pending. This record establishes local implementation and review evidence only.

- Review follow-up: retirement guard is acquired atomically with the event failure check, before helper entry; deterministic helper-entry cleanup test passes. Device-bound failed-retirement markers are installed before the preliminary SQL read; read-failure/recovery tests cover the matching device and rejection of a wrong device without retiring its owner’s claim. Reviewer verified both corrections and reported no remaining blockers in them.
- `done` here records the local terminal-safeguard slice. F1 and F2 are separately authorized follow-up slices and are not completed by this status. Live pilot acceptance remains pending.
