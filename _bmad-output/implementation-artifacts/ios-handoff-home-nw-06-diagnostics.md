# iOS hand-off — HOME-NW-06 connection-failure diagnostics (client side)

Status: cross-surface brief, derived from the Home implementation in this worktree
(`.worktrees/home-connection-diagnostics`). Home code is the authority cited below;
the spec (`_bmad-output/specs/spec-connection-failure-diagnostics/event-contract.md`)
is marked "proposed" and its divergences are listed in §5. The durable shared
decision still needs to be filed in the private product hub (§6).

## 1. Wire contract Home implements

### 1.1 Opt-in handshake
- Header: `X-Hermes-Diagnostics-Version: 1` on the authenticated HomeBridge
  WebSocket upgrade (`/api/v1/bridge/ws`).
- `src/hermes_home/api/bridge_server.py:_diagnostics_opted_in` — opted in only when
  the header appears **exactly once** (case-insensitive name) with the literal value
  `"1"`. Duplicate headers, `"01"`, `"1.0"` etc. → legacy socket.
- `src/hermes_home/bridge/endpoint.py` `__init__` / rebind path (~l.296, ~l.578)
  stores the opt-in per socket. Header is feature negotiation only; never authority.
- Not opted in → every response is byte-for-byte the legacy shape; any
  request `diagnostics` object is dropped and counted (`schema_rejected`), the RPC
  itself proceeds normally (`endpoint.py:_diagnostic_metadata`).

### 1.2 Ready / reconnect response (opted-in sockets only)
`endpoint.py` request handler (~l.649–665): for `conversation.open` and
`conversation.reconnect` when `result.status == "ready"`:
- `result.capabilities` gains `"diagnostics_correlation_v1": true` and
  `"client_diagnostic_report_schemas": [1, 2]` (merged into the existing capability
  object; if upstream supplied no mapping, the object contains only these two keys).
- Top-level JSON-RPC envelope gains
  `"diagnostics": {"version": 1, "home_connection_id": "conn-<32 lowercase hex>"}`.
- `home_connection_id` = `self._diagnostic_connection_id`: per-socket ID from
  `bridge_server` (validated `conn-[0-9a-f]{32}`) or freshly generated. Same physical
  socket → same ID across reconnect; new socket / rebind → new ID. Client can never
  choose it.
- Non-ready results (`unavailable`) and errors carry no ready diagnostics.

### 1.3 Request (`prompt.submit` only is answered)
Top-level envelope key (sibling of `jsonrpc`/`schema`/`id`/`method`/`params`, never in
`params`):
```json
"diagnostics": {"version": 1, "request_id": "req-<32 lowercase hex>"}
```
`endpoint.py:_diagnostic_metadata` validation: object keys exactly
`{version, request_id}`; `version` must be `int` 1 (Boolean rejected);
`request_id` must fullmatch `req-[0-9a-f]{32}`. Invalid → ignored + counted, RPC
unaffected. Repeat of a token on the same socket → marked ambiguous
(`correlation_conflicts`). After 4096 tokens per socket → further tokens ignored
(counted as `schema_rejected`).

### 1.4 Response
`endpoint.py` (~l.666–677): only for `prompt.submit`, opted-in socket, valid
non-ambiguous token, and a Home correlation exists. Applies to **both** `result`
and `error` responses:
```json
"diagnostics": {"version": 1, "request_id": "req-…", "correlation_id": "corr-<32hex>"}
```
Ambiguous (duplicate) tokens get no response diagnostics. Other methods never get
response diagnostics. Metadata is diagnostic only — not auth, dedupe or retry state.

### 1.5 Delayed association (what makes the report "linked")
`endpoint.py:_record_client_association` → `client_reports.py:ClientReportStore.record_association`
stores `(authenticated device, home_connection_id, request_id) → (correlation_id,
process_id, observed_at, expires_at, state)` when the request is observed (before the
response write). Initial state is **`provisional`**. `_finalize_client_associations`
(called on transport detach ~l.525, rebind ~l.572, close ~l.884) promotes
`provisional` → `linked` and marks duplicates `ambiguous`. Bounds: 1024 rows/device,
4096 total, 7-day expiry (`RETENTION`). Lookup (`_lookup_association`) requires exact
device + `home_connection_id` + `request_id`; anything else → `unavailable`.

### 1.6 Report upload: `POST /api/v1/client-diagnostics`
`api/application.py:_post_client_diagnostics`, `api/server.py` body limit
`MAX_BODY` = 65 536 bytes for this path. Receipt is still
`{"schema": 1, "report_id": …}` for schema-2 reports. 429 `rate_limited` if < 30 s
since the device's last stored report; 400 `invalid_request` for any validation
failure.

`client_reports.py:validate_report` — schema 2 exactly:

Report top-level keys exactly: `schema`(int 2), `report_id`(lowercase-canonical UUID),
`created_at`(number, epoch s, within [now−7 d, now+300 s]), `app_version`, `build`,
`os_version` (each `^[0-9]{1,8}(\.[0-9]{1,8}){0,3}$`), `platform` (`ios`|`macos`),
`model` (`(iPhone|iPad|Mac)N,N` | `arm64` | `x86_64` | `unknown`), `events`, `origins`.
Whole body ≤ 65 536 UTF-8 bytes.

`origins` (`_origins`): list of 1–100; each object keys **exactly**
`launch_id, app_version, build_number, os_version, source_revision, artifact_sha256,
provenance_status`. `launch_id` UUID, unique (case-insensitive) in the list.
`provenance_status` ∈ `verified|unverified|unavailable`. `app_version`,
`build_number`, `os_version`: version-regex string, or `null` **only if** status is
`unavailable`. `source_revision`: `null` or 40/64 lowercase hex. `artifact_sha256`:
`null` or 64 lowercase hex. (Unreferenced origins are not rejected.)

`events`: list of 1–100. Allowed keys (`SCHEMA2_FIELDS`), unknown → reject:
| key | rule |
|---|---|
| `time` (req) | number, epoch seconds, within [now−7 d, now+300 s] |
| `name` (req) | `launch, active, inactive, background, connection_lost, connection_ready, connection_failed, request_started, request_completed, request_failed, client_response_received, client_request_resolved` |
| `launch_id` (req) | UUID, must appear in `origins` |
| `event_id` (req) | `evt-[0-9a-f]{32}` |
| `sequence` (req) | int 0…2^63−1 |
| `connection_id`, `home_connection_id` | `conn-[0-9a-f]{32}` |
| `request_id` | `req-[0-9a-f]{32}` |
| `correlation_id` | `corr-[0-9a-f]{32}` |
| `correlation_state` | `local_only, linked, ambiguous, unavailable` |
| `leg` | `client_home, home_proxy, proxy_standard, process` |
| `pending_state` | `awaiting_write, awaiting_response, response_resolved, stream_active, none, unknown` |
| `response_kind` | `accepted|rejection`, only on `client_response_received`/`client_request_resolved` |
| `phase` | `open, reconnect, submission, lifecycle, startup, response, stream, closing, closed, shutdown` |
| `code` | existing `CODES` (= iOS `ClientDiagnosticEvent.allowedCodes`) |
| `duration_ms` | int 0…86 400 000 |
| `uncertain` | Bool |
| `sent_close_code`, `received_close_code` | `null` or int 1000–4999 excluding 1005/1006/1015 |
| `observed_status_code` | `null` or int 1000–4999 (1006 etc. allowed here) |

Identity rules (`receive`, `_reject_conflicting_events`): same `event_id` must carry
identical content within a report and across all stored schema-2 reports of that
device; same `launch_id` origin must be identical across stored reports; re-upload of a
stored `report_id` with different content → 400 (identical → idempotent 200).
Schema 1 is unchanged and still accepted.

Viewer: `ClientReportStore.recent` → `/pair/api/client-diagnostics` (signed-in
`/pair` page, `pairing_page.py` `#client-reports`) attaches per event
`{event_id, state, correlation_id?, process_id?, observed_at?}`; lookup only runs for
schema-2 events carrying both `home_connection_id` and `request_id`.

## 2. iOS changes, in order

1. **Decoder allowlists first (before ever sending the header).**
   - `HermesRelay/Services/HomeBridgeSessionClient.swift:handleTextFrame` — response
     branch `requireKeys(object, allowed: ["jsonrpc","schema","id","result","error"])`:
     add `"diagnostics"`; validate it strictly in `decodeResponse` (ready shape
     `{version:1, home_connection_id}` or submit shape
     `{version:1, request_id, correlation_id}`; exact keys, int version, regexes).
     Malformed diagnostics should be dropped (diagnostic-only), not fail the RPC —
     decide and test explicitly. Leave the notification branch unchanged (Home adds
     nothing to notifications).
   - `HermesRelay/Models/HomeBridgeModels.swift:HomeWireCapabilities` — add optional
     `diagnosticsCorrelationV1` (`diagnostics_correlation_v1`) and
     `clientDiagnosticReportSchemas` (`client_diagnostic_report_schemas`, `[Int]`) to
     `CodingKeys` (enforced by private `HomeCoding.requireExactKeys`, same file
     ~l.1529); thread into `HomeBridgeCapabilities` (~l.504).
   - Reconnect capability comparison (`HomeBridgeSessionClient.swift` ~l.1544–1560,
     `capabilitiesMatch`): exclude the two diagnostics fields from the equality that
     decides `capabilitiesMatch`, otherwise a Home restart/downgrade or a new socket
     without opt-in turns into a spurious reconnect mismatch.
2. **Send the header** in the WebSocket `URLRequest` (`HomeBridgeSessionClient.swift`
   ~l.922, next to `Authorization`): `X-Hermes-Diagnostics-Version: 1`, exactly once.
3. **Capture `home_connection_id` before declaring ready.** In the open/reconnect paths
   that return `.ready(binding:capabilities:)` (~l.287, ~l.348–400, ~l.452–495) store
   the per-socket `home_connection_id` on the binding/session state before emitting
   ready; clear it on socket close/rebind; a new socket must use the new ID from its
   own ready. Only treat diagnostics as negotiated when capability
   `diagnostics_correlation_v1 == true`, `2 ∈ client_diagnostic_report_schemas`, and
   the envelope ID is present and valid.
4. **Stamp pre-send events.** For each `prompt.submit` (~l.552) on a negotiated socket:
   generate `req-` + 32 lowercase hex (128-bit random), add top-level `diagnostics`
   to the request, and record the schema-2 `request_started` (and later
   `client_response_received`, `client_request_resolved`, `request_failed`, …) events
   with `home_connection_id`, `request_id`, local `connection_id`, `leg:
   "client_home"`, `phase: "submission"`, `correlation_state: "local_only"` **before**
   writing the frame, so a lost response keeps the join. When the response carries
   matching diagnostics, record `correlation_id`. Never reuse a token on a socket.
5. **Schema-2 model + packing** in `HermesRelay/Services/AutomaticDiagnostics.swift`:
   - `ClientDiagnosticEvent`: add `event_id` (`evt-`+32hex, assigned once at
     observation and persisted), `sequence` (monotonic per launch, persisted), the ID /
     enum / close fields above, two new `Name` cases. Never mutate a persisted event
     (Home rejects conflicting `event_id` content across reports).
   - `ClientDiagnosticReport.make`: `schema: 2`, add `origins` (one per referenced
     `launch_id`, all seven keys; use `provenance_status: "unavailable"` with nulls
     when source revision/digest are unknown; versions must match the regex or be
     null+unavailable). Origin content per `launch_id` must be stable forever.
   - Packing: deterministic serializer (no whitespace, sorted keys), events in
     observation order, greedily add whole events + newly needed origins while ≤ 100
     events and ≤ 65 536 encoded body bytes; seal and continue into the next snapshot
     slot (existing ≤10 per Home, `queuePendingSnapshot`); drop single events that
     cannot fit alone and count the loss; store the sealed bytes and retry the exact
     bytes (`flush` / `ClientDiagnosticUploader.send`). Keep 30 s server rate limit in
     mind (429 → keep report).
   - `ClientDiagnosticUploader.send` receipt check `receipt.schema == 1` remains
     correct for schema-2 uploads.
6. **Legacy behavior.** Home without the capability (or not ready, or no valid
   `home_connection_id`): send no request `diagnostics`, produce schema-1 reports
   (or schema-2 without `home_connection_id`/`request_id`, only if Home advertised
   schema 2). Old Home ignores the header. Report schema choice is per Home pairing,
   from that Home's last advertised `client_diagnostic_report_schemas`.
7. `HermesRelay/Views/AutomaticDiagnosticsSettings.swift`: disclosure copy should
   mention random connection/request identifiers are now sent (no content); opt-in,
   deletion and 7-day expiry unchanged.

## 3. Device acceptance
1. Run this Home build; pair the iPhone; enable automatic connection reports for the
   Home; sign in to Home `/pair`.
2. Connect a conversation. Confirm (debug log / test hook) the ready envelope held a
   `conn-…` `home_connection_id` and capability `diagnostics_correlation_v1`.
3. Submit a prompt; confirm the request carried `req-…` and the response returned the
   same `request_id` plus `corr-…`.
4. Induce a failure after submit (e.g. stop Hermes Standard / kill network mid-turn) so
   `request_failed`/`connection_lost` queues a snapshot, then restore and let the app
   flush in foreground.
5. **Ensure the Home socket that carried the request has closed** (background the app,
   or reconnect on a new socket) — Home only promotes associations to `linked` at
   socket finalize (§5 D1).
6. In `/pair`, the report's event with that `request_id` shows association
   `state: linked` with the `corr-…` matching Home's operational log for the request.
   A legacy Home or unpaired device must still accept schema-1 reports; a duplicate
   token test shows `ambiguous`.
7. Regression: connect to a Home build without this feature — no header-driven
   change, no decode failures, no reconnect mismatch.

## 4. Home source map
- `src/hermes_home/api/bridge_server.py`: `_diagnostics_opted_in`, socket
  `connection_id` plumbed into endpoint.
- `src/hermes_home/bridge/endpoint.py`: `__init__`/rebind opt-in, ready/submit
  injection block, `_diagnostic_metadata`, `_record_client_association`,
  `_finalize_client_associations`.
- `src/hermes_home/observability/client_reports.py`: `validate_report`, `_origins`,
  `_schema2_event`, `ClientReportStore.receive/recent/_lookup_association`.
- `src/hermes_home/api/application.py:_post_client_diagnostics`; `api/pairing.py`
  `/pair/api/client-diagnostics`.

## 5. Spec vs Home-implementation divergences
- **D1 — association state timing.** Spec: a unique association proves Home observed
  the request; lookup returns linked. Impl: rows are `provisional` until
  `_finalize_client_associations` runs on transport detach/rebind/close; until then
  the viewer reports `unavailable`. Also if Home crashes before finalize, the row
  stays provisional (→ unavailable) permanently.
- **D2 — token cap.** Spec: after 4096 live tokens "mark further links unavailable".
  Impl ignores them and counts `schema_rejected`, not a distinct state/counter.
  Left as is: the spec names no counter, and a new loss counter would change the
  operational record schema. Delayed joins for capped tokens resolve `unavailable`.
- **D3 — ready capabilities fallback. FIXED.** Home now adds the two diagnostics
  keys only to an existing capabilities mapping and never synthesizes one. On the
  ready path Home always projects `commands`/`timing` (defaults `[]`/`absent`), so
  iOS `HomeWireCapabilities` decoding holds; `diagnostics.home_connection_id` is
  still sent. Covered by
  `test_opted_in_ready_adds_diagnostics_only_to_upstream_capabilities`.
- **D4 — receipt schema.** Spec is silent; Home returns `schema: 1` receipts for
  schema-2 reports (iOS check must not be "upgraded" to 2).
- **D5 — origins strictness.** Spec: "emit only origins referenced by packed events";
  validator requires every event's origin but does not reject unreferenced ones, and
  requires all seven origin keys present (nullable), with null versions allowed only
  under `provenance_status: unavailable`.
- **D6 — header duplicates.** Spec says Home accepts only literal `1`; impl also
  requires exactly one header occurrence.
- **D7 — schema-2 event names.** Spec catalog lists many operational events
  (`request_observed`, `transport_observed`, …); report schema 2 only adds
  `client_response_received` and `client_request_resolved` to the schema-1 names.
  Report `time` stays epoch-seconds number (spec's RFC3339 `time_utc` applies to Home
  operational records only).
- **D8 — response diagnostics scope.** Only `prompt.submit` responses (result or
  error) carry response diagnostics; the spec's generic "results/errors optionally
  return" should be read as submit-only.

## 6. Filing
Shared protocol contract belongs in the private product hub (Personal Vault Hermes
Home hub): `~/Documents/Vaults/Personal Vault/projects/hermes-home/`, next to
`Hermes Home Event Compatibility Matrix.md` / `approved-delivery-contract-2026-09-23.md`.
Then one focused iOS story in `hermes-relay-ios`.
