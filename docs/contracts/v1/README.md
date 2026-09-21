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
- `conversation_active` — this Room already has an active conversation; stop or
  wait for that conversation to close before waking again;
- `claim_denied` — a valid claim lost arbitration or failed eligibility;
- `service_unavailable` — the service cannot safely answer the request.
