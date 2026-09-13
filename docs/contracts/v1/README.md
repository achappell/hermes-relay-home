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
    "wake_mappings": [
      {"id": "hey-hermes", "name": "Hey Hermes"}
    ],
    "devices": [
      {
        "id": "puck-kitchen",
        "name": "Kitchen Puck",
        "room_id": "kitchen",
        "profile_id": "family",
        "priority": 1,
        "capabilities": {"wake_claim": true}
      }
    ]
  }
}
```

`PUT /api/v1/configuration` replaces the complete configuration atomically:

```json
{
  "schema": 1,
  "expected_revision": 12,
  "snapshot": {
    "rooms": [],
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

IDs are opaque, stable strings. `priority` is a positive integer unique within
each Room; `1` is the highest priority. Every Device has one Room, one Profile
reference, and explicit capabilities. A valid publish is all-or-nothing.

## Wake arbitration

`POST /api/v1/wake-claims` submits one claim and waits for the bounded
arbitration result:

```json
{
  "schema": 1,
  "claim_id": "claim-01J...",
  "device_id": "puck-kitchen",
  "wake_mapping_id": "hey-hermes",
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

The first valid claim opens a 250 ms window. The service compares eligible
claims by acoustic proximity evidence and uses configured Device priority to
resolve an effective tie. The response is tied to the submitted claim:

```json
{
  "schema": 1,
  "claim_id": "claim-01J...",
  "decision": "granted",
  "arbitration_id": "arb-01J...",
  "configuration_revision": 13
}
```

`decision` is either `granted` or `denied`. A denied, malformed, late,
revoked, unavailable, or unmapped claim fails closed. A failed winner does not
promote a loser. The claimant may proceed to acknowledgement/capture only
after a grant for its own claim ID.

The claim contains no Profile ID, wake phrase, prompt, transcript, or audio.
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
- `claim_denied` — a valid claim lost arbitration or failed eligibility;
- `service_unavailable` — the service cannot safely answer the request.
