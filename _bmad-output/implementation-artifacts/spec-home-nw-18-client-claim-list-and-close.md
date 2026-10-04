---
title: 'HOME-NW-18 — List and close a personal client''s open Home conversations'
type: 'feature'
created: '2026-10-03'
status: 'review'
baseline_commit: '07de094'
route: 'dispatch'
review_loop_iteration: 1
context:
  - '{project-root}/AGENTS.md'
  - '{project-root}/docs/contracts/v1/README.md'
  - '{project-root}/_bmad-output/implementation-artifacts/course-correction-2026-09-23.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-home-nw-05-profile-mappings-conversation-claims.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-home-nw-17-client-pairing-and-direct-admission.md'
---

# Implementation Spec: HOME-NW-18

## Intent

**Problem:** A personal client can create client claims (HOME-NW-17), but it can't see them or release one it no longer needs. The only way to end a claim is `conversation.close` on a bridge connection that is bound to it. A claim that was created but never opened has no bridge binding, so the client can't release it. It holds one of the device's slots until the 90-second first-open expiry.

On 2026-09-27, an iPad running app 0.5.0 (Profile `spark`) showed what happens. A client-side refusal loop created seven claims in 19 seconds, and none of them were opened. Each was `activity = 'ready'` with no Session and a 90-second deadline. Those seven, plus the live conversation, reached the per-device limit of 8 (`HERMES_HOME_CLIENT_CLAIMS_PER_DEVICE`), and the next `POST /api/v1/client-claims` was refused `409 claim_limit`. The app couldn't tell the user what was open or let them clear it. IOS-HOME-03 slice A fixed the client loop. Slices B and C are blocked on this story:

- **B:** release a held but unopened claim when the user starts or switches a conversation, or disconnects;
- **C:** show "Open on Home (N of 8)" for this device, with Close and Close all others, and offer it when a connect is refused with `claim_limit`.

**Approach:** Add a Home-minted opaque `claim_ref` to every new client claim, and return it from `POST /api/v1/client-claims`. Add two device-authenticated routes on the existing client-claim surface:

- `GET /api/v1/client-claims` lists the caller's own active client claims: Profile label, session reference, creation and first-open times, and a four-value state taken from the stored claim activity plus whether the claim's endpoint is currently parked after a client socket drop.
- `POST /api/v1/client-claims/close` closes an explicit set of the caller's own claims by `claim_ref`. It uses the same notifying close path as revocation, so a live or parked bridge stops its Standard turn and its client sees a terminal `stale_conversation`. The Hermes Session stays stored and resumable.

## Boundaries & Constraints

**Always:** Authenticate both routes like every other client route (`_client_context`: durable Device credential, current generation, `client_claim` capability). Scope every read and close to `device_id = caller AND claim_kind = 'client'`. Report a claim that isn't an active client claim of the caller the same way whether it is unknown, closed, expired, a wake/touch claim, or another device's claim. Sweep due expiries before listing or closing, so the list count is the same count `create_client_claim` checks against the limit. Close live and parked claims through the revocation-handler path, so an open bridge interrupts its Standard turn and its client sees `stale_conversation`. Keep the first `close_reason` a claim receives. Keep the closed claim's Standard Session and its `session_ref` mapping. Closing a claim only ends Home's authority over it.

**Never:** Return a `conversation_handle`, Profile ID, Standard Session ID, prompt, reply, or transcript from either route. Accept a handle, `claim_id`, Profile ID, or Room on the close route. Close, list, or reveal another device's claims, or Room claims (wake, touch). Delete or truncate a Hermes Session. Replay a turn that was interrupted by a close. Log, label a metric with, or record in diagnostics a `claim_ref`, handle, handle prefix, `session_ref`, or `grant_id`. Call Standard Hermes from either route. Change the NW-17 lifecycle rules: no idle timer on client claims, the 90-second first-open expiry, the 120-second reconnect grace, and the per-device limit all stay as they are. Add a sibling-repository change.

**Compatibility decision:** Evolve `/api/v1/*` in place. The create response gains one field (`claim_ref`), and clients that ignore unknown fields are unaffected. A client detects support from `claim_ref` in the create response: if it is absent, Home predates HOME-NW-18, the client must not call the list or close routes, and those routes answer `404 not_found` there. Both new paths sit under the `/api/v1/client-claims` prefix that `deploy/windows/README.md` already publishes through Tailscale Serve, so deployment needs no new Serve path. Every claim is closed `service_restart` when Home starts (`ConversationGrantStore.__init__`), so after the deploy every active client claim has a `claim_ref`. Rows from earlier claims keep `claim_ref = NULL`.

## Resolved Direction

- **A Home-minted `claim_ref`, not the handle or `claim_id`.** No such reference exists today. A claim row is keyed by `handle`, which is `secrets.token_urlsafe(32)` and is the bearer authority that `conversation.open` and `conversation.reconnect` accept, and by `claim_id`, which the client chooses (`UNIQUE` across all devices). Listing handles would turn a read route into a source of open authority. `claim_id` is a client-formatted request identifier whose shape Home doesn't control across TUI, iOS, macOS and Android. Because it is globally unique, it would also be an existence oracle for another device's claims if any route answered differently for it. Instead, Home mints `claim_ref = "cref-" + secrets.token_urlsafe(18)` at creation (factory injectable for tests), the same pattern as `session_ref` (`"sref-" + token_urlsafe(18)`). The ref authorizes nothing on the bridge. It is accepted only by the close route, and only together with the owning Device credential.
- **The list never calls Standard; the client joins titles (approved by Amanda, 2026-10-03).** Home doesn't store titles. A title exists only in Standard's `session.list`, which `StandardSessionDirectory` reaches over a new short-lived gateway connection per Profile. The list is most needed when something is wrong, possibly while Standard is unhealthy, so it must not depend on Standard. Each item carries the grant-scoped `session_ref` (or `null` before the first accepted turn, when the Session is not yet persisted). The client joins it against `POST /api/v1/client-sessions/list` for that `grant_id`. Titles are best effort: that route needs an active grant and an available Profile, calls Standard, and returns at most 50 non-background sessions, so a row whose `session_ref` is `null` or missing from it shows no title, and the list must render without titles. IOS-HOME-03 slice C currently says each row shows the "conversation title if any" as if Home returned it; it must be amended to say the title comes from `client-sessions/list` and may be absent. The list is not strictly read-only: resolving `session_ref` may insert a `client_session_refs` row, as `client-sessions/list` already does.
- **No extra route for a lost create response (approved by Amanda, 2026-10-03).** If `POST /api/v1/client-claims` times out on the client, the claim may exist with a `claim_ref` the client never received. It shows in the list as `connecting`, can be closed from there, and expires within 90 seconds; IOS-HOME-03 slice B serializes connects so only one can be in flight. The close route does not accept `claim_id`.
- **The state comes from the stored `activity` column plus the parking lot.** `activity` is the claim's durable state, written only by the store (`create_client_claim`, `mark_open`, `record_activity`, `mark_disconnected`). A client socket drop does not touch it: `BridgeEndpoint.run` detaches a ready endpoint (`endpoint.py` `has_recoverable_state`), and `api/bridge_server.py` `_EndpointParkingLot` keeps it and its live `HomeBridge` for the parking grace (`HERMES_HOME_CLIENT_RECONNECT_GRACE_SECONDS`, `runtime.py`), with the Standard gateway and revocation handler intact. Adoption through `HomeBridge.reauthorize` doesn't call the store either. Only when the parking entry expires does `endpoint.close()` → `bridge.close()` → `mark_disconnected` set `activity = 'disconnected'` and start the store's own reconnect grace, and by then the bridge has unregistered its handler. So the store gains an in-memory, content-free detached marker (see Code Map), and the list derives `state` as:

  | Condition | Written by | `state` |
  | --- | --- | --- |
  | handle is marked detached (endpoint parked), any `activity` | `mark_detached` on park; cleared by `clear_detached` on adoption, close, or park expiry | `waiting_to_reconnect` |
  | `activity = 'disconnected'` | `mark_disconnected` after Standard-side transport loss, or after a parked endpoint expired and its bridge closed; `idle_deadline` = now + reconnect grace | `waiting_to_reconnect` |
  | `activity = 'ready'` | claim created; `mark_disconnected` deliberately leaves it unchanged | `connecting` |
  | `activity = 'open'` | `mark_open` after `session.create`/`session.resume` succeeds | `idle` |
  | `activity` in `response_ready`, `idle` | turn finished (`response_ready`); client reported `playback_complete`/`idle`; client claims get no idle deadline | `idle` |
  | `activity` in `turn`, `capture`, `playback` | prompt accepted (`turn`); client reported capture or playback | `replying` |

  The detached marker wins over `activity`. `capture` and `playback` map to `replying` because a turn is in progress and closing it interrupts work. `response_ready` maps to `idle` because a text client never reports `playback_complete`, so a finished text turn stays at `response_ready`. The marker must not set an `idle_deadline`: adoption never calls `mark_open`, so a deadline would expire a claim whose adopted bridge is live. A parked endpoint shows `waiting_to_reconnect` immediately through the detached marker, and stays `waiting_to_reconnect` after park expiry through `activity = 'disconnected'`. After a client drop, a claim can therefore hold its slot for up to the parking grace plus the store grace (about 240 s by default).
- **Times are wall-clock seconds stored at the event.** The store's `clock` is `time.monotonic` by default (`runtime.py` passes no clock), and the conversion from monotonic time drifts across host sleep. Two new `REAL` columns are added in the same migration as `claim_ref`: `created_wall_at`, set at create, and `opened_at`, set by `mark_open` on the first successful open only (later reconnects don't change it). Both use an injectable `wall_clock` (default `time.time`). The list returns `created_at` and `opened_at`, both rounded to whole seconds, with `opened_at = null` while the claim is `connecting` and has never opened.
- **Close takes an explicit set of refs, and "Close all others" is that set.** `claim_refs` holds 1–64 unique refs (64 is the store's upper bound for the per-device limit). "Close all others" is the client sending every listed ref except its current one. This is one round trip, and it never closes a claim the user didn't see, such as a claim made by another window of the same device after the list was fetched. A server-side "all except X" would close those. A single close is a one-element list.
- **Close is idempotent and per-ref.** The response has one result per requested ref, in request order. `closed` means it was an active client claim of the caller and is now closed with `close_reason = 'client_closed'`. `not_open` covers every other case: unknown, already closed or expired, a wake or touch claim, or another device's claim. Repeating a close returns `not_open`. The HTTP status is 200 whenever the request is valid and authenticated. Per-ref answers can't be used to probe another device, because its refs read `not_open` like random strings do.
- **The first close reason sticks.** Today `_close_locked` updates `WHERE handle = ?` with no status filter, and `close_claim` returns `True` for already-closed rows, so follow-on cleanup (`retire_failed_upstream` → `upstream_lost`, `_failed_open` → `session_startup_failed`, `_invalidate_binding` → `authorization_revoked`) would overwrite `client_closed`. The pilot diagnosis was built from these rows. `_close_locked` must update only `status = 'active'` rows, and `close_claim` on a row that exists but is already closed is a no-op that keeps the first `close_reason` and still returns `True`, so callers that treat `False` as failure (`_failed_open`, `close_conversation`, `_discard_new_claim` callers) behave as before.
- **Client close only is terminal `stale_conversation`.** For `client_closed`, the handler sends `session.interrupt`, closes the gateway, and marks the binding closed; `BridgeClaimClosed` maps to `stale_conversation` and close 1000. Other revocations and stale-binding failures retain the pre-NW-18 upstream-failure path (`reconnect_required`, 1011). Closing a live or parked client claim keeps the Session stored and resumable.
- **One bridge socket binds one handle.** `BridgeEndpoint` refuses `conversation.open` or `conversation.reconnect` with a different handle once bound (`conversation_mismatch`; `_bound_handle` is never reset), and `api/bridge_server.py` builds a new `HomeBridge` per connection. Switching conversations always uses a new socket, so `HomeBridge._open`'s switch branch is unreachable from clients. As a defensive guard, the revocation handler is registered per handle and `_on_claim_revoked` returns early when the revoked handle differs from the bridge's current `_conversation_handle`. Without it, a switch on one `HomeBridge` would leave the old handle's handler pointing at the bridge, and closing the old claim would tear down the new conversation.
- **The limit comes from Home.** `HERMES_HOME_CLIENT_CLAIMS_PER_DEVICE` is configurable from 1 to 64. The list returns it as `max_claims`, so the client shows "N of `max_claims`" instead of a hard-coded 8.
- **No grant or configuration gate on close.** A device can always close its own claims, even if their grant is pending or revoked, their Profile is unavailable, or its configuration revision is stale. Those conditions already close claims through their own sweeps. Blocking a close on them would only strand a slot.
- **The list degrades by field.** The list returns `503` only when the claim store read fails. A configuration read failure (including `configuration_migration_required`) makes `profile_label` `null` for every row, and a `session_ref` resolution failure makes that row's `session_ref` `null`. Clients can also join labels by `grant_id` from device configuration's `client_grants`.
- **Close latency is serialized and may exceed 15 seconds.** The claim rows are committed closed before notification handlers run. Handlers then run serially on the HTTP request thread. Each handler may wait for an in-flight open or reconnect to finish its Standard round trips while holding that bridge's lifecycle lock, then wait up to 1 second for `session.interrupt`. Latency can grow with the number of live claims. A caller whose request times out must re-list before retrying or assuming which claims remain open.

## Proposed Contract

### `POST /api/v1/client-claims` (response addition)

```json
{
  "schema": 1,
  "claim_id": "client-01J...",
  "claim_ref": "cref-3q2_7wK...",
  "decision": "granted",
  "configuration_revision": 13,
  "conversation_handle": "opaque-home-claim-01J...",
  "session": {"mode": "new"}
}
```

The request and every denial are unchanged. `client-claim.schema.json` describes only the request, so it is also unchanged.

### `GET /api/v1/client-claims`

Device-authenticated (`Authorization: Device …`), no body.

```json
{
  "schema": 1,
  "max_claims": 8,
  "claims": [
    {
      "claim_ref": "cref-3q2_7wK...",
      "grant_id": "grant-01J...",
      "profile_label": "Spark",
      "session_ref": "sref-01J...",
      "created_at": 1727398071,
      "opened_at": 1727398072,
      "state": "idle"
    },
    {
      "claim_ref": "cref-9xk...",
      "grant_id": "grant-01J...",
      "profile_label": "Spark",
      "session_ref": null,
      "created_at": 1727398076,
      "opened_at": null,
      "state": "connecting"
    }
  ]
}
```

The list holds every active, unexpired client claim of the caller, newest first by `created_at`; it is already bounded by `max_claims`, so there is no truncation. `state` is one of `connecting`, `idle`, `replying`, `waiting_to_reconnect`. `profile_label` is the configured Profile `name`, or `null` if the Profile has been removed from configuration but the claim has not yet been closed, or if configuration can't be read. `session_ref` is grant-scoped, as in `client-sessions/list`. `opened_at` is the first successful open, or `null` if the claim has never opened.

### `POST /api/v1/client-claims/close`

```json
{"schema": 1, "claim_refs": ["cref-9xk...", "cref-a71..."]}
```

```json
{
  "schema": 1,
  "results": [
    {"claim_ref": "cref-9xk...", "result": "closed"},
    {"claim_ref": "cref-a71...", "result": "not_open"}
  ]
}
```

### What the closed claim's client sees

| Claim state when closed | Client-visible outcome on the bridge socket |
| --- | --- |
| `connecting`, no socket yet | A later `conversation.open` returns `status: unavailable`, `reason: stale_conversation` |
| `connecting`, `conversation.open` in flight | The open returns `status: unavailable`, `reason: stale_conversation`; the newly created Standard session is interrupted |
| `idle` or `replying` on a live socket | `session.interrupt` is sent upstream; the endpoint becomes unavailable with reason `stale_conversation` and closes the socket with code 1000; never `reconnect_required`, never 1011 |
| `waiting_to_reconnect`, endpoint parked | `session.interrupt` is sent upstream; the parking entry is evicted; a later `conversation.open` or `conversation.reconnect` returns `status: unavailable`, `reason: stale_conversation` |
| `waiting_to_reconnect`, `activity = 'disconnected'` (no bridge) | Row closed and grace timer cancelled; a later `conversation.reconnect` returns `status: unavailable`, `reason: stale_conversation` |

In every case the stored `close_reason` is `client_closed`.

### Errors

Both routes use the existing envelope `{"schema": 1, "error": {"code": …}}` from `_error`. They don't use `_claim_submission_error`, which maps only wake-arbitration reasons.

| Code | Status | When |
| --- | --- | --- |
| `unauthorized` | 401 | Missing, revoked, expired, or replaced (overlap-window) credential |
| `client_claim_unavailable` | 403 | Credential lacks `client_claim`, or Home runs without the credential service |
| `invalid_request` | 400 | Non-JSON close body, extra or missing fields, `claim_refs` empty, over 64, duplicated, or holding a non-string or a string outside 1–128 characters |
| `service_unavailable` | 503 | Claim store or credential store failed (configuration and `session_ref` failures degrade fields instead) |

There is no per-ref `404`. An unknown ref is a `not_open` result. On a Home without HOME-NW-18 both paths answer `404 not_found`.

## I/O & Edge-Case Matrix

| Situation | Behavior | Guardrail |
| --- | --- | --- |
| Pilot case: 7 unopened claims plus 1 live, then a new connect | Create returns `409 claim_limit`; the list shows 7 `connecting` and 1 `idle`/`replying`; the client closes the 7 and creates successfully | List count = the active count `create_client_claim` checks, after the same expiry sweep |
| Client holds an unopened claim and the user starts, switches, or disconnects | Client closes it by `claim_ref`; result `closed`; slot freed immediately | No bridge involvement; the first-open timer is cancelled |
| Client socket drops on an open claim | Endpoint parked; list shows `waiting_to_reconnect` while parked and, after park expiry, while `activity = 'disconnected'` | Detached marker; slot may be held for parking grace plus store grace |
| Close a `replying` claim held by another window or app instance on the same device | Claim closed; that bridge sends `session.interrupt`; its client sees `stale_conversation` and a 1000 close | Turn not replayed; Session kept and resumable (`active: false` in the session list); `close_reason` stays `client_closed` |
| Close a parked claim | Closed; the parked bridge's handler interrupts it; the parking entry is evicted; a later reconnect gets `stale_conversation` | No `reconnect_required` for a closed claim |
| Close a claim with `activity = 'disconnected'` | Closed; no handler exists (the bridge already closed); grace timer cancelled; a later reconnect gets `stale_conversation` | — |
| Close races a `conversation.open` in progress on the same claim | The close waits for the bridge's lifecycle lock; `mark_open` finds the claim inactive, so the open fails and its new Standard session is interrupted; no live conversation on a closed claim | Existing `mark_open` status check and `_failed_open` path; `close_reason` stays `client_closed` |
| Close and create race on a full device | Either order is valid; the limit is never exceeded | Both run under `BEGIN IMMEDIATE` |
| Close the caller's own bound claim over HTTP | Allowed; the caller's socket gets the live-close frames above | Clients should prefer `conversation.close` for their bound claim |
| Close the same ref twice | First `closed`, then `not_open` | Idempotent; 200 both times |
| Ref of another device's claim | `not_open`; that claim is untouched | Same answer as a random string |
| Ref of a claim past its deadline whose timer hasn't fired yet | Expiry sweep closes it with its expiry reason; result `not_open` | Same sweep as create |
| Wake or touch claim on the device | Never listed; can't be closed here | `claim_kind = 'client'` filter; those rows have no `claim_ref` |
| Credential revoked, re-enrolled, or renewed | Revocation and re-enrollment already close all of the device's claims; renewal closes the previous generation's claims; old credential gets `401` | Existing `close_device_claims` paths |
| Credential expired | `401`; claims can't be listed until the device pairs again, and re-pairing closes them | No new authority path |
| Grant revoked or pending, or Profile unavailable | Claims already closed by their sweeps; anything left is still listed and closable | Close has no grant or configuration gate |
| Configuration unreadable or migration required | List still answers with `profile_label: null` | Field degradation, not 503 |
| No claims | `{"claims": [], "max_claims": 8}` | — |
| Standard Hermes down | List and close still answer; closing a live claim still ends Home authority, but its `session.interrupt` may fail and is logged without identifiers | No Standard call on either route |
| Request through Tailscale Serve (`X-Forwarded-For`) | Both routes answer normally | Not admin routes; never `admin_local_only` |
| Home without HOME-NW-18 | No `claim_ref` in the create response; both routes `404 not_found` | Client feature detection |
| Pre-NW-18 claim rows | Closed at startup; never listed | `claim_ref` NULL only on closed history |

## Code Map

- `src/hermes_home/bridge/production.py` (`ConversationGrantStore`):
  - Add `claim_ref TEXT`, `created_wall_at REAL` and `opened_at REAL` to `_CLAIMS_TABLE_SQL`. Whether or not the NW-17 rebuild ran (`_migrate_claims_table` currently returns early when `claim_kind` exists), add each missing column with `ALTER TABLE conversation_claims ADD COLUMN …`, then `CREATE UNIQUE INDEX IF NOT EXISTS conversation_claims_claim_ref ON conversation_claims(claim_ref) WHERE claim_ref IS NOT NULL`.
  - Add the constructor arguments `claim_ref_factory` (default `"cref-" + secrets.token_urlsafe(18)`) and `wall_clock` (default `time.time`).
  - `create_client_claim` mints and inserts `claim_ref` and `created_wall_at`, and returns `claim_ref` with the handle. Callers of the current single-handle return change with it: `_post_client_claim`, `tests/test_client_claim_store.py`, and `tests/test_pairing_page.py`.
  - `mark_open` sets `opened_at` from `wall_clock` only when it is `NULL`.
  - `_close_locked` updates only `status = 'active'` rows; `close_claim` on an existing, already-closed row is a no-op that keeps the first `close_reason` and returns `True`.
  - In-memory detached marker: `mark_detached(handle, device_id)` and `clear_detached(handle)`, held like `_watch_connected_at`, never written to SQLite and never setting a deadline; cleared on every close path and on `mark_open`.
  - New `client_claims(device_id) -> list[...]`: runs `_expire_due_locked`, then selects active `claim_kind = 'client'` rows for the device. It returns `claim_ref`, `grant_id`, `profile_id`, `session_id`, `created_wall_at`, `opened_at`, and the `state` mapped as in Resolved Direction (detached marker first).
  - New `close_client_claims(device_id, claim_refs, *, reason="client_closed") -> frozenset[str]`: sweeps due expiries, then closes with predicate `device_id = ? AND claim_kind = 'client' AND claim_ref IN (…)`. It uses the body of `_close_matching_claims`, which selects and updates in one `BEGIN IMMEDIATE` and calls revocation handlers after releasing the store lock. Factor that body so it can also return the closed rows' `claim_ref`s; the existing callers that return a count stay unchanged. `close_client_claims` must not hold the store lock while those handlers run. `_on_claim_revoked` takes the bridge's `_lifecycle_lock`, and `HomeBridge._open` holds that lock while it calls `mark_open`, which needs the store lock, so holding both would deadlock.
  - Add a read-only `max_client_claims` property.
- `src/hermes_home/bridge/standard.py` (`HomeBridge`):
  - `_on_claim_revoked` marks the binding revoked; `next_event` on a revoked binding raises a new `BridgeClaimClosed` (reason `stale_conversation`) instead of `RuntimeError("home bridge is not ready")`.
  - Register the revocation handler as a per-handle closure, and return early from `_on_claim_revoked` when the revoked handle differs from `_conversation_handle`.
- `src/hermes_home/bridge/endpoint.py` (`BridgeEndpoint`):
  - Map `BridgeClaimClosed` from `next_event` (and from request paths) to `_mark_unavailable("stale_conversation")` and a 1000 close. It must not enter `_retire_failed_upstream`, must not call `retire_failed_upstream`, and must not log the "Home upstream unavailable" line or any handle prefix.
  - `detach()` calls the store's `mark_detached` through an injected callback; `adopt()` and `close()` call `clear_detached`.
- `src/hermes_home/api/bridge_server.py` (`_EndpointParkingLot`):
  - Add `evict(handle)`, which removes and closes a parked endpoint. When the store closes a claim through `close_client_claims` (or any notifying close), the parked entry for that handle is evicted, so `conversation.open` for it no longer answers `reconnect_required`.
  - On park expiry, clear the detached marker before closing the endpoint.
- `src/hermes_home/runtime.py`: wire the store's detached-marker callbacks into the bridge factory and the parking lot's eviction into the store's close notification.
- `src/hermes_home/api/application.py`:
  - `_post_client_claim` adds `claim_ref` to the response.
  - Dispatch `GET /api/v1/client-claims` to `_get_client_claims` and `POST /api/v1/client-claims/close` to `_post_client_claim_close`. Both start with `_client_context`.
  - The list resolves `profile_label` with `_profile_labels(snapshot)` and `session_ref` with `session_ref(grant_id, session_id)`, the same call `client-sessions/list` uses; each degrades to `null` on failure.
  - `_metric_route` maps `/api/v1/client-claims/close` to `client_claim_close`. `GET /api/v1/client-claims` is already labelled `client_claims`, with `method="GET"`.
  - Diagnostics: `_record_diagnostic` passes `_metric_route(path)` as `route_id`, and `client_claims`, `client_sessions` and `client_claim_close` are not in `_SAFE_ROUTE_IDS` (`observability/diagnostics.py`), so these routes record no diagnostic event today (validation fails and is swallowed). Add `client_claims` and `client_claim_close` to `_SAFE_ROUTE_IDS`, and map client-claim error codes onto existing `_FAILURE_CODES` (`client_claim_unavailable` → `forbidden`, `claim_limit` → `claim_denied`), so the content-safety acceptance below is not passed trivially.
- `src/hermes_home/domain/conversations.py`: the `ConversationClaimStore` Protocol is unchanged. Client-claim routes already use the SQLite store directly, as `create_client_claim` and `client_claim_binding` do today.
- `docs/contracts/v1/README.md`: under "Personal clients", document `claim_ref` on create and feature detection, the list and close routes, the state table, the client-visible close frames, the explicit-set close semantics, and that closing keeps the Session. Add `client_closed` wherever close reasons are listed.
- Tests: `tests/test_client_claim_store.py`, `tests/test_client_claims_api.py`, `tests/test_standard_bridge.py` (live-revocation precedent near `live-revocation-handle`), and the endpoint and bridge-server tests: cover the cases in Tasks & Acceptance.
- No change to `deploy/windows/*` or `client-claim.schema.json`.

## Tasks & Acceptance

**Execution:**

1. Store: `claim_ref`, `created_wall_at` and `opened_at` columns, migration, unique partial index, minting at create, and the injectable factories.
2. Store: first-close-reason-sticks fix in `_close_locked`/`close_claim`; the detached marker.
3. Store: `client_claims` with the expiry sweep and state mapping, plus `max_client_claims`.
4. Store: `close_client_claims` through the notifying close path, with `client_closed` as the reason.
5. Bridge and endpoint: `BridgeClaimClosed`, terminal `stale_conversation` mapping, per-handle revocation guard, detach/adopt marker calls.
6. Bridge server and runtime: parking-lot eviction on close and marker clearing on expiry; wiring.
7. API: `claim_ref` in the create response, `GET /api/v1/client-claims`, `POST /api/v1/client-claims/close`, the metric route label, and diagnostic route IDs.
8. Contract documentation.

**Acceptance Criteria:**

- The pilot scenario reproduces, then clears: with `max_claims` 8, eight claims (seven never opened) make the next create return `claim_limit`. The list shows seven `connecting` and one other. Closing the seven by `claim_ref` returns seven `closed`, and the next create succeeds.
- Each stored `activity` value maps to its specified `state`, and the test covers every value an active row can hold; the detached marker overrides every value.
- A client socket drop on an open claim, followed by a list, shows `waiting_to_reconnect` while the endpoint is parked, and still after park expiry while `activity = 'disconnected'`.
- Closing a parked claim interrupts its gateway and evicts the parking entry; the next `conversation.reconnect` gets `stale_conversation`.
- Endpoint level, through `BridgeEndpoint`: an HTTP close of a live mid-turn claim sends `session.interrupt` to Standard; the client sees reason `stale_conversation` and close code 1000, never `reconnect_required` or 1011; the stored `close_reason` is `client_closed`; the Session is in `client-sessions/list` with `active: false`, and `resume` works.
- A close racing an in-flight `conversation.open` makes the open fail with `stale_conversation`, interrupts the new Standard session, returns after the open finishes, and leaves `close_reason = 'client_closed'`.
- A concurrent close and create on a full device succeed in either order without exceeding the limit.
- Closing an unopened claim cancels its first-open timer and frees the slot at once.
- A ref belonging to another device, a wake/touch claim, an expired claim, an already-closed claim and a random string all return `not_open`, and the other device's claim stays active.
- Device A's list never contains device B's claims, and wake or touch claims on the same device are never listed.
- `max_claims` reflects a non-default `HERMES_HOME_CLIENT_CLAIMS_PER_DEVICE` (for example 3).
- Close-body validation returns `400 invalid_request` for an empty list, 65 refs, a duplicate, a non-string, an extra field, and a wrong content type.
- Proxied requests (`X-Forwarded-For`) to both routes are answered normally and never get `admin_local_only`.
- A configuration read failure or `configuration_migration_required` during list returns 200 with `profile_label: null`.
- `created_at` is set at create; `opened_at` is `null` until the first open, set once, and unchanged by a later reconnect.
- Unit test on one `HomeBridge`: open A, switch to B, close A through `close_client_claims`; B stays ready.
- Neither route returns a handle, Profile ID or Session ID. A test asserts that no `claim_ref`, handle, handle prefix or `session_ref` appears in captured logs, metrics output, or diagnostic events for these requests, including the live and parked close paths, and that diagnostic events are actually recorded for both routes.
- Revoked, replaced and expired credentials get `401`. A credential without `client_claim` gets `403 client_claim_unavailable`.
- Both routes answer while the session directory is absent or failing.
- An existing NW-17 database migrates in place (claim row history kept, new columns NULL on old rows). A second start is a no-op.
- Focused tests, `ruff check src tests`, and `ruff format --check src tests` pass.

## Consuming Stories

- iOS IOS-HOME-03:
  - Slice B: detect support from `claim_ref` in the create response, store it, and close an unopened held claim by `claim_ref` when starting, switching or disconnecting.
  - Slice C: the "Open on Home (N of `max_claims`)" section from the list route, marking the current claim by its stored `claim_ref`, showing `opened_at` (or `created_at` while connecting). Close and Close all others send an explicit `claim_refs` set. On `claim_limit`, offer Manage open conversations. Titles are joined from `client-sessions/list` by `session_ref` and may be absent; IOS-HOME-03 slice C must be amended to say so.
  - A close request may wait behind an in-flight open/reconnect and notification handlers for multiple refs run serially, so it may exceed 15 seconds. If the request times out, re-list before retrying or assuming which claims remain open.
  - Never log `claim_ref`, handles or `session_ref`.
- TUI, macOS and Android may adopt the same routes. Nothing here requires them to.

## Spec Change Log

- 2026-10-03: Drafted from IOS-HOME-03's dependency and the 2026-09-27 iPad pilot claim leak, grounded in `ConversationGrantStore`, `HomeBridge`, and the NW-17 client routes at `07de094`.
- 2026-10-03: Adversarial review applied (F1–F12): state derivation includes the endpoint parking lot; live close is terminal `stale_conversation` at the endpoint; first close reason sticks; title join resolved as a recommended default awaiting confirmation; one-socket-one-handle replaces the switching decision, with a per-handle revocation guard; feature detection via `claim_ref`; diagnostics route IDs; handle-prefix log safety; `created_wall_at`/`opened_at` columns; field-level degradation of the list; close latency guidance; Code Map corrections.
- 2026-10-03: Amanda approved both open decisions: titles by client join (no Standard call in the list), and no extra route for a lost create response. Status `ready-for-dev`.

## Verification

Implemented on `feat/home-nw-18-client-claim-list-close` (`59fc9ee`). See [`validation-home-nw-18.md`](validation-home-nw-18.md): focused suites 341 passed, full suite 798 passed, `ruff check` and `ruff format --check` clean. Implementation deviations from this text are listed there. Status `review`; not deployed.
