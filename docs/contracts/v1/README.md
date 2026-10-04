# Home HTTP contract v1

The wire contract is versioned in the URL and every JSON document carries
`schema: 1`. JSON is UTF-8 and requests must use `Content-Type:
application/json`.

## Authentication

Administrative routes use a dedicated Home-service admin bearer credential:

```http
Authorization: Bearer <home-admin-token>
```

This credential is distinct from the Hermes relay token and from every
device-scoped wake credential. iOS stores it in Keychain. It must not appear in
URLs, JSON bodies, snapshots, logs, or diagnostics.

Claim routes use the authenticated device credential established out of band:

```http
Authorization: Device <device-credential>
```

Credential issuance, rotation, and revocation propagation are service/device
implementation work and are not encoded in the claim body.

## Configuration

`GET /api/v1/configuration` returns the currently active snapshot:

```json
{
  "schema": 1,
  "snapshot": {
    "revision": 12,
    "rooms": [
      {"id": "kitchen", "name": "Kitchen"}
    ],
    "profiles": [
      {"id": "family", "name": "Family", "available": true}
    ],
    "wake_mappings": [
      {
        "id": "hey-hermes",
        "phrase": "Hey Hermes",
        "profile_id": "family",
        "active": true
      }
    ],
    "devices": [
      {
        "id": "puck-kitchen",
        "name": "Kitchen Puck",
        "room_id": "kitchen",
        "priority": 1,
        "capabilities": {"wake_claim": true}
      }
    ]
  }
}
```

The optional Device capability `interactive_choice` defaults to `false`.
Set it to `true` only for a Device with an active user interface that can
present and confirm choices. Passive Room Displays and audio-only Pucks remain
read-only for typed choices unless their role changes deliberately.

The optional Device capabilities `sensitive_entry` and
`consequence_confirm` also default to `false`. A protected prompt is authorized
only when the current Device configuration and the current credential scope
both contain the matching capability; omission from either side denies it.
`secret.request` and `sudo.request` accept one current value of at most 4096
UTF-8 bytes through the existing structured response operation, while
`approval.request` requires `consequence_confirm` even when it contains no
secret value. Home exposes only an allowlisted prompt projection and a fixed
safe response envelope. Protected values are not written to ordinary
transcripts, history, Watch output, or diagnostics, and the upstream Standard
operation must provide the same privacy boundary; Home does not replace that
storage guarantee.

`PUT /api/v1/configuration` replaces the complete configuration atomically:

```json
{
  "schema": 1,
  "expected_revision": 12,
  "snapshot": {
    "rooms": [],
    "profiles": [],
    "wake_mappings": [],
    "devices": []
  }
}
```

The candidate snapshot does not supply its own revision. On success the server
returns the activated snapshot with its new revision. A stale
`expected_revision` returns HTTP `409` and does not change state:

```json
{
  "schema": 1,
  "error": {
    "code": "revision_conflict",
    "current_revision": 13
  }
}
```

IDs are opaque, stable strings. Each available Profile is household-wide.
Each Wake Mapping names one phrase and one Profile; active phrases are unique
after Unicode compatibility normalization, whitespace folding, and case
folding. Several distinct phrases may map to the same Profile. A Device has
one Room, a positive priority unique within that Room (`1` is highest), and
explicit capabilities. Profile and mapping edits publish atomically.

Existing v1 snapshots with a Device `profile_id` cannot be migrated safely:
mapping labels do not identify Profile targets. Reads fail with
`configuration_migration_required` and the current revision; an administrator
must publish the new shape using that revision. Home never guesses the target.

Paired-device approval uses an explicit mapping choice in the `scope` object:

```json
{
  "schema": 1,
  "scope": {
    "rooms": ["kitchen"],
    "capabilities": ["wake_claim"],
    "wake_mapping_grant": {"mode": "selected", "ids": ["hey-hermes"]}
  }
}
```

The alternative `{"mode":"all_current_profiles"}` is expanded to the active
mapping IDs for currently available Profiles at approval time. Home stores only
those exact IDs. Later mappings require a new approval. A paired Device reads
`GET /api/v1/devices/{device_id}/configuration`; Home returns its current
revision and only its authorized active mappings, with no Profile IDs. Devices
fetch on pairing/startup/reconnect, poll every 30 seconds while active, and
refresh after `stale_configuration` or `stale_mapping`.

A Touch-capable approval may also include one Home-selected Room/Profile pair:

```json
{
  "schema": 1,
  "scope": {
    "rooms": ["kitchen"],
    "capabilities": ["touch_claim"],
    "wake_mapping_grant": {"mode": "selected", "ids": []},
    "touch_binding": {"room_id": "kitchen", "profile_id": "family"}
  }
}
```

The binding is valid only when its Room is in the approved Room scope and its
Profile is currently available. An absent binding means the endpoint has no
Touch grant. Home does not include the binding or Profile ID in endpoint
configuration; it remains Home-side authorization state.

The independent `watch_view` credential capability permits one bounded read-only observation of an endpoint in an authorized Room. It does not grant wake claims, prompts, choices, interruption, configuration changes, microphone access, audio, or transcript replay. The capability is checked against the observer's current credential generation and Room scope on every request.

The independent `health_view` credential capability permits one bounded,
read-only health check for one endpoint in an authorized Room. It does not
grant wake claims, prompts, choices, interruption, configuration changes,
microphone access, audio, or transcript replay. Home checks the target's
route, Home authorization, bridge readiness, and Standard Hermes readiness as
separate stages. Device-local microphone, speaker, wake-listener, and display
checks remain `unsupported` until an endpoint adapter owns those probes.

## Watch View

`GET /api/v1/devices/{device_id}/watch` returns one current snapshot for an authorized endpoint. The response contains only a configured display label, a safe route and health state, and a bounded content-free task summary:

```json
{
  "schema": 1,
  "watch": {
    "status": "available",
    "endpoint": {"id": "display-living", "name": "Living Display"},
    "profile_label": "Family",
    "route": {"class": "home", "id": "local"},
    "health": "ready",
    "task": {
      "state": "turn",
      "summary": "Processing the current turn",
      "session_present": true
    },
    "preview": {
      "kind": "safe_state",
      "summary": "Processing the current turn"
    }
  }
}
```

If no eligible current state exists, the authenticated request returns the same envelope with `watch.status: "unavailable"` and one of `no_current_state`, `stale_state`, or `observation_unavailable`. Revoked, expired, stale, or disconnected state never falls back to another endpoint or Profile. The response never contains Profile IDs, prompts, transcripts, raw audio, credentials, Sensitive Entry values, private notifications, or unrelated room content. The endpoint remains read-only; ongoing transcript/status fan-out is a separate future contract.

## Device Health

`GET /api/v1/devices/{device_id}/health` runs one bounded check for an
authorized endpoint. The caller must hold `health_view`, and the target must
be in one of the caller's authorized Rooms. A successful request returns HTTP
`200` even when a boundary is unavailable so clients can explain the failure
without treating a transport error as a healthy device:

```json
{
  "schema": 1,
  "health": {
    "status": "degraded",
    "correlation_id": "corr-01J...",
    "stages": [
      {"name": "route", "status": "verified"},
      {"name": "authorization", "status": "verified"},
      {"name": "bridge", "status": "verified"},
      {"name": "standard", "status": "verified"},
      {
        "name": "device_local",
        "status": "unsupported",
        "reason": "unsupported",
        "next_action": "check_endpoint_capabilities"
      }
    ],
    "delivery": {"status": "active", "activity": "turn"}
  }
}
```

Stage results use only `verified`, `unavailable`, `stale`, `timed_out`, and
`unsupported`, with an allowlisted reason and safe next action when needed.
The health status describes the check just completed; `delivery` is a separate
read-only view of any current or uncertain delivery. The check never opens,
resumes, closes, or replays a conversation, and never captures audio or sends
a prompt. Standard readiness uses a fresh `gateway.ready` and read-only
`gateway.ping` connection, which is closed when the check finishes. The full
document is capped at 16 KiB; an oversized result becomes an unavailable
projection with `response_too_large` and no probe payload.

## Wake arbitration

`POST /api/v1/wake-claims` submits one claim and waits for the bounded
arbitration result:

```json
{
  "schema": 1,
  "claim_id": "claim-01J...",
  "device_id": "puck-kitchen",
  "wake_mapping_id": "hey-hermes",
  "configuration_revision": 13,
  "observation": {
    "detector": "device-local",
    "observed_at_ms": 1720000000000
  },
  "acoustic_evidence": {
    "kind": "opaque-v1",
    "value": 0.91
  },
  "availability": "ready"
}
```

The first valid claim opens a 250 ms window in its Room. Other Rooms arbitrate
independently. The service compares eligible claims by acoustic proximity
evidence and uses configured Device priority to resolve an effective tie. A
claim must name its current configuration revision; stale snapshots are
rejected. The response is tied to the submitted claim:

```json
{
  "schema": 1,
  "claim_id": "claim-01J...",
  "decision": "granted",
  "arbitration_id": "arb-01J...",
  "configuration_revision": 13,
  "conversation_handle": "opaque-home-claim-01J..."
}
```

`decision` is either `granted` or `denied`. A denied, malformed, late,
revoked, unavailable, or unmapped claim fails closed. A failed winner does not
promote a loser. The claimant may proceed to acknowledgement/capture only
after a grant for its own claim ID.

The claim contains no Profile ID, wake phrase, prompt, transcript, or audio.
The endpoint's authorized configuration snapshot contains only mapping IDs and
phrases. Home resolves the Profile and Session after the winner is selected.
On a granted claim, the response also contains a claim-specific opaque
`conversation_handle`; denied claims have no handle. The endpoint opens that
handle on the Home bridge route and begins capture only after `ready`. The
bridge exposes only the opaque handle and safe readiness or failure state. A
granted handle expires if its first successful open does not happen within 90
seconds; expiry closes the claim and releases its Room.

The bridge accepts content-free `conversation.activity` updates with `state`
`capture`, `turn`, `playback`, or `playback_complete`. Playback completion is
the device's acknowledgement that starts or resets the default 8-second idle
timer. `conversation.close` explicitly stops the logical conversation. A Home
bridge transport reconnect can resume the same claim; it does not create a new
wake or a new Session.

The server uses its monotonic receive clock; Device wall-clock values are
observation metadata only. Wi-Fi RSSI is not accepted as physical proximity.

The foundation implementation interprets a finite numeric `value` in the
`acoustic_evidence` object as a deterministic proximity score, with higher
values closer. Equal scores use the configured Device priority; this is the
provisional policy used by deterministic tests, not the final hardware
encoding or calibration contract.

The exact acoustic evidence encoding, calibration, normalization, and tie band
remain a hardware-contract decision. The field is intentionally isolated so
that decision does not leak into the mobile configuration API.

## Touch admission

`POST /api/v1/touch-claims` admits a physical tap from an authenticated Touch
endpoint. The request is deliberately smaller than a wake claim:

```json
{
  "schema": 1,
  "claim_id": "touch-01J...",
  "device_id": "touch-kitchen",
  "configuration_revision": 13,
  "initiation": {"kind": "tap", "observed_at_ms": 1720000000000}
}
```

The endpoint sends no Profile ID, Session ID, wake mapping, mapping phrase, or
acoustic evidence. Home checks the current configuration, the endpoint's
`touch_claim` capability, and its bound Room/Profile pair before granting.
Admission is synchronous; there is no arbitration window or `arbitration_id`.

A successful response contains the opaque handle used by the existing bridge:

```json
{
  "schema": 1,
  "claim_id": "touch-01J...",
  "decision": "granted",
  "configuration_revision": 13,
  "conversation_handle": "opaque-home-claim-01J..."
}
```

The Room is live while capture, turn processing, playback, response-ready, or
an unopened granted claim is active; a Touch claim is denied with
`room_busy` (or `conversation_active` for a repeat from the same endpoint).
After `playback_complete`, Home keeps an eight-second idle tail. A valid Touch
claim during that tail closes the old claim as `superseded_by_touch` and opens
the new one. Once the tail expires, it no longer blocks admission.

## Personal clients (HOME-NW-17)

TUI, iOS, macOS, and Android clients are paired personal clients. They hold the
`client_claim` capability and one grant per approved Profile. They belong to no
Room, never take part in arbitration, and never block, preempt, or supersede a
Room conversation.

### Pairing

The Home pairing page (`GET /pair`) creates an offer and shows a pairing link,
a QR code of that link, and a short code for typing:

```text
hermes-home://pair?home=https%3A%2F%2Fhome.example.ts.net&code=K7Q4MX2PNV
```

The client submits the existing `POST /api/v1/enrollment/requests` body with
`type` of `tui`, `ios`, `macos`, or `android` and `requested_capabilities` containing
`client_claim`. Short codes are accepted in any case, with or without the dash.
The client shows the returned confirmation code and polls
`POST /api/v1/enrollment/requests/{request_id}/consume`:

- `409 approval_pending` — keep waiting; the page has not approved yet;
- `403 rejected` — the request was rejected; start again;
- `410 expired_or_consumed` — the five-minute window closed; start again;
- `200` — credential material plus `client_grants`.

```json
{
  "schema": 1,
  "device_id": "id-7",
  "credential": "...",
  "generation": 1,
  "expires_at": 1735000000.0,
  "scope": {"rooms": [], "capabilities": ["client_claim"], "wake_mappings": []},
  "client_grants": [
    {"grant_id": "grant-01J...", "label": "Amanda", "status": "active", "available": true},
    {"grant_id": "grant-01K...", "label": "Jensen", "status": "pending_owner", "available": true}
  ]
}
```

Clients must store the credential in the platform secure store and keep it per
Home. A Device credential is always 32 random bytes encoded as 43 unpadded
base64url characters; clients may validate that shape. Profile IDs never reach a client; `grant_id` and `label` do.

**Staying paired.** Credentials last 90 days. A client renews itself with
`POST /api/v1/devices/{device_id}/credentials/renew` during the last 14 days,
normally on connect. A client unused for 90 days must pair again. Revocation
from the page, or re-enrollment, ends the pairing.

**Profile ownership.** A Profile with `"shared": true` in configuration can be
granted on the page directly. Any other Profile is owned: the first device to
receive it is a recorded bootstrap, and every later grant is
`pending_owner` until a device already holding that Profile approves it.

`GET /api/v1/devices/{device_id}/configuration` returns the current
`client_grants` beside the configuration `revision`.

### `POST /api/v1/client-claims`

```json
{
  "schema": 1,
  "claim_id": "client-01J...",
  "device_id": "id-7",
  "configuration_revision": 13,
  "grant_id": "grant-01J...",
  "session": {"mode": "resume", "session_ref": "sref-01J..."}
}
```

`session` is optional: `{"mode": "new"}` (the default), `{"mode":
"most_recent"}`, or `{"mode": "resume", "session_ref": ...}`. The response adds
the opaque handle, a `claim_ref` (HOME-NW-18), and the chosen session:

```json
{
  "schema": 1,
  "claim_id": "client-01J...",
  "claim_ref": "cref-3q2_7wK...",
  "decision": "granted",
  "configuration_revision": 13,
  "conversation_handle": "opaque-home-claim-01J...",
  "session": {"mode": "resumed", "session_ref": "sref-01J..."}
}
```

The handle opens the existing bridge with `conversation.open`. A client claim
has no idle timer. It closes on `conversation.close`, when the client stays
disconnected longer than the reconnect grace (default 120 s), or on
revocation. To switch sessions, a client closes its claim and makes a new one.
Rename the current session with Hermes's advertised `title` command through
`command.dispatch`.

Denials: `client_claim_unavailable` (403), `grant_pending` (409),
`stale_configuration` (409), `profile_unavailable` (409), `claim_limit`
(409, default 8 per device), `session_unavailable` (404, unknown reference for
this grant), and `session_busy` (409, another active claim holds that session).

### `POST /api/v1/client-sessions/list`

```json
{"schema": 1, "grant_id": "grant-01J...", "limit": 20}
```

```json
{"schema": 1, "sessions": [
  {"session_ref": "sref-01J...", "title": "Groceries", "started_at": 1727120000, "message_count": 14, "active": false}
]}
```

The list is the Profile's conversations from every surface (Room devices and
other clients), newest first, 1–50 entries. `session_ref` values are opaque and
valid only for the grant that received them. `active` marks a session another
active claim holds; resuming it returns `session_busy`.

### `POST /api/v1/client-claims/session`

```json
{"schema": 1, "conversation_handle": "opaque-home-claim-01J..."}
```

```json
{"schema": 1, "session_ref": "sref-01J..."}
```

Names the session behind the caller's own active client claim, so a client can
resume that conversation later with `{"mode": "resume", "session_ref": ...}`. A
new claim has no session until its first accepted turn, so `session_ref` is
`null` until then. A handle that is not an active client claim of the calling
device returns `404 not_found`; grant denials match the client-claim route.

### `GET /api/v1/client-claims` (HOME-NW-18)

Device-authenticated, no body. Lists the caller's own active client claims,
newest first; the count is the count the per-device limit checks.

```json
{"schema": 1, "max_claims": 8, "claims": [
  {"claim_ref": "cref-3q2_7wK...", "grant_id": "grant-01J...",
   "profile_label": "Spark", "session_ref": "sref-01J...",
   "created_at": 1727398071, "opened_at": 1727398072, "state": "idle"}
]}
```

`state` is `connecting` (never opened), `idle`, `replying` (a turn, capture or
playback in progress), or `waiting_to_reconnect`. A claim waits to reconnect
from the moment its client socket drops (the endpoint is parked) until it
reconnects or the store's reconnect grace ends, so a dropped claim can hold its
slot for up to two grace periods (about 240 s by default). `created_at` and
`opened_at` are wall-clock seconds; `opened_at` is the first successful open,
`null` while connecting. `profile_label` is `null` when configuration can't be
read, and `session_ref` is `null` before the first accepted turn. The list never
calls Standard, so it carries no titles: join `session_ref` against
`client-sessions/list` for a title, which may be absent.

### `POST /api/v1/client-claims/close` (HOME-NW-18)

```json
{"schema": 1, "claim_refs": ["cref-9xk...", "cref-a71..."]}
```

```json
{"schema": 1, "results": [
  {"claim_ref": "cref-9xk...", "result": "closed"},
  {"claim_ref": "cref-a71...", "result": "not_open"}
]}
```

Closes an explicit set of 1–64 unique refs ("Close all others" is every listed
ref but the current one). `closed` means it was an active client claim of the
caller and is now closed with reason `client_closed`; `not_open` covers every
other case, including another device's refs, so repeating a close is safe.
Invalid bodies get `400 invalid_request`; the routes otherwise share the
client-claim denials (`401 unauthorized`, `403 client_claim_unavailable`,
`503 service_unavailable`). Closing keeps the Hermes Session: it stays in
`client-sessions/list` with `active: false` and can be resumed.

The closed claim's client sees a terminal result: a live socket gets
`session.interrupt` upstream, becomes unavailable with reason
`stale_conversation`, and closes with code 1000; a parked or later
`conversation.open`/`conversation.reconnect` answers `stale_conversation`.
A close is committed before notification handlers run, but those handlers run
serially. Each live handler can wait for an in-flight open or reconnect to
finish its Standard round trips while holding the bridge lifecycle lock, then
wait up to one second for `session.interrupt`; latency can therefore grow with
the number of live claims and exceed 15 seconds. If a close request times out,
re-list before retrying or assuming which claims remain open. Prefer
`conversation.close` for the claim a socket is bound to.

A client detects these routes from `claim_ref` in the create response; older
Home answers both with `404 not_found`.

### Profile grants

A device that holds an active grant for a Profile can manage that Profile's
other grants:

- `GET /api/v1/profile-grants/pending` — grants waiting for this owner;
- `GET /api/v1/profile-grants/holders` — every device holding this device's
  Profiles, by label and type only;
- `POST /api/v1/profile-grants/{grant_id}/approve`, `/reject`, `/revoke` with
  `{"schema": 1}`.

A pending grant expires after 24 hours. Revoking a grant closes its claims.

## Errors

All error documents use the versioned envelope and a stable `error.code`.
Initial codes are:

- `invalid_request` — malformed JSON or invalid known fields;
- `unauthorized` — missing or invalid credential;
- `not_found` — unknown Room, Device, or wake mapping;
- `revision_conflict` — stale configuration publish;
- `configuration_migration_required` — old configuration needs an explicit
  trusted publish in the new shape; configuration reads and wake claims include
  `current_revision` so the administrator can publish against the stored row;
- `stale_configuration` — refresh the authorized Device snapshot before
  submitting another claim;
- `stale_mapping` — the wake mapping exists but is inactive; refresh the
  authorized Device snapshot before submitting another claim;
- `touch_claim_unavailable` — this endpoint has no approved Touch capability
  and Room/Profile binding;
- `room_busy` — the Room has a live conversation or an unexpired idle tail;
- `profile_unavailable` — the approved Touch Profile is no longer available;
- `conversation_active` — this Room already has an active conversation; stop or
  wait for that conversation to close before waking again;
- `claim_denied` — a valid claim lost arbitration or failed eligibility;
- `client_claim_unavailable`, `grant_pending`, `claim_limit`,
  `session_unavailable`, `session_busy`, `approval_pending`, `rejected` —
  personal-client pairing and claims, described above;
- `admin_local_only` — an admin-token route was called through a proxy; use
  the pairing page or loopback;
- `service_unavailable` — the service cannot safely answer the request.

## Automatic client connection reports

`POST /api/v1/client-diagnostics` accepts `Authorization: Device <credential>` from an active paired device. The device identity is derived from that credential, never from the body. This endpoint does not accept an admin token and exposes no device-readable report route. The client must obtain explicit per-Home opt-in before using it.

Schema 1 has exactly these fields: `schema` (1), `report_id` (UUID), `created_at` (Unix seconds), `app_version`, `build`, `platform` (`ios`/`macos`), `os_version`, `model`, and `events`. Version values are numeric dot-separated components; model values are Apple model identifiers, simulator architecture, or `unknown`. A report contains 1–100 events, each with Unix `time`, UUID `launch_id`, and `name`: `launch`, `active`, `inactive`, `background`, `connection_ready`, `connection_failed`, `connection_lost`, `request_started`, `request_completed`, or `request_failed`. Optional fields are an allowlisted `code`, integer `duration_ms` (0–86,400,000), `phase` (`open`, `reconnect`, `submission`, `lifecycle`), and Boolean `uncertain`. Unknown fields, arbitrary text, prompts, audio, credentials, and conversation handles are rejected. Client timestamps must fall within the last seven days or at most five minutes ahead of Home.

Success is HTTP 200 with `{"schema":1,"report_id":"<same UUID>"}`. Retrying the same report ID for the same authenticated device is idempotent, including after a restart. Failures use the usual error envelope: 400 invalid payload, 401 unauthorized, 429 rate limited, or 503 storage unavailable. The client retains a report until it receives the matching acknowledgment and retries at a bounded foreground cadence; it never retries a conversation as part of reporting.

Requests are capped at 64 KiB. Home accepts at most one new report per device per 30 seconds, stores at most 100 reports per device and 1,000 total, and expires reports seven days after receipt (pruned on read/write). The signed-in `/pair` page reviews the latest 50 reports through same-origin `POST /pair/api/client-diagnostics` and displays the latest 100 safe Home events alongside them. Times can be compared, but this slice does not establish per-turn correlation between client reports and Home events. Automatic connection reports are separate from content-bearing incident capture.
