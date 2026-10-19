# Paired-room wake admission contract

Owner-approved 2026-10-19; Home NW-05 owns grants/policy, TUI P4/3-P-5 owns the bridge consumer. This is the explicit paired-room mode only. Strict wake, Touch, browser and personal-client contracts remain unchanged.

## Revisioned approval

The existing local-admin revision-checked configuration API may add this optional field to the approved Device record:

```json
{"wake_admission":{"mode":"paired_room","endpoint_id":"ops-hermes-puck-bridge","wake_mapping_ids":["hey-missy"]}}
```

The Device record supplies its Room. The approval is bound to that exact Home Device ID and durable credential endpoint ID, its current Room and selected configured mapping IDs; no code hard-codes a Device UUID. At most one paired-room approval may be configured. Unknown modes/fields, empty/duplicate mapping IDs and unavailable approval targets are invalid. Omission means existing strict behavior, never implicit paired approval. All approval changes publish a new configuration revision.

The actual owner-approved target is Device `c9dd86cfa9634330bf412e560969b7a9`, endpoint `ops-hermes-puck-bridge`, Room `living-room`, selected `hey-missy` → Amanda. This paragraph is deployment intent, not automatic approval or source-code allowlisting.

## Authenticated discovery

`GET /api/v1/devices/{device_id}/configuration` uses that Device credential and projects paired policy **inside snapshot**, alongside the existing revision/mappings:

```json
{"schema":1,"snapshot":{"revision":100,"wake_mappings":[{"id":"hey-missy","phrase":"Hey Missy"}],"wake_admission":{"mode":"paired_room","room_id":"living-room","wake_mapping_ids":["hey-missy"]}}}
```

No new `device_id` field appears; credential and URL bind identity. Home verifies policy endpoint/Room/mapping and current credential scope. Configured paired policy with invalid binding, scope or exclusivity returns `403 forbidden`, not silent omission. Removing policy yields the ordinary snapshot without `wake_admission`, at the new revision. A consumer holding paired authority must stop/invalidate it on omission, revision drift, error or policy/binding change; it must not silently fall back to strict admission without the calibrated provider.

## Fresh grant before capture

`POST /api/v1/paired-room-claims`, authenticated with the own Device credential, accepts exactly:

```json
{"schema":1,"claim_id":"fresh-claim-id","device_id":"c9dd86cfa9634330bf412e560969b7a9","wake_mapping_id":"hey-missy","configuration_revision":100,"initiation":{"kind":"wake_word"}}
```

Schema and revision are strict integers, not booleans. There is no acoustic evidence, numeric score, confidence or touch initiation. Unknown fields are rejected. Successful response:

```json
{"schema":1,"claim_id":"fresh-claim-id","decision":"granted","arbitration_id":"opaque-admission-id","configuration_revision":100,"conversation_handle":"opaque-home-issued-handle","admission_mode":"paired_room"}
```

The bridge requires matching claim ID and current verified revision, exact `paired_room` mode and nonempty Home handle. It completes the existing authorized conversation-open/ready checks before capture. It never fabricates a handle, uses another endpoint's credential or retries an uncertain submitted turn. Claim IDs remain single-use under the existing durable claim uniqueness and room ownership machinery. Existing deadlines, credential generation binding, revocation, close/recovery and no-replay apply.

## Eligibility and coexistence

Home intersects current active credential `wake_claim`/Room/mapping scope with the configured wake-enabled Device, explicit endpoint-bound policy, active mapping and available Profile. The request revision must equal current Home revision. Home rereads current policy/revision and authorization before issuing/returning a fresh grant; an invalidated newly created claim is retired, not returned.

Paired admission is eligible only if this endpoint is the **sole configured and currently credential-authorized wake-enabled Device for the same mapping anywhere in the household**. Another eligible Device for `hey-missy`, in any Room, causes denial; Home does not rank or invent proximity. Strict Devices authorized only for different mappings are unchanged. At most one explicit paired policy is configured. A busy Room remains busy under existing claim rules.

Removing/changing policy, Room, endpoint/mapping binding or verified revision invalidates affected paired claims and prevents their continued bridge authorization. Current eligibility is rechecked when a paired claim is resolved, including competing credential authorization changes. Strict claims retain their prior rules. This conservative exclusivity is an implementation consequence of the approved single-Puck scope, not a quotation or approval of proximity-free multi-device selection.

## Errors

The existing envelope is retained: `{"schema":1,"error":{"code":"..."}}`.

- `400 invalid_request`: malformed/unknown fields, wrong types or initiation.
- `401 unauthorized`: missing, invalid, expired/revoked credential or wrong Device identity.
- `403 forbidden`: no explicit paired approval, endpoint/Room/mapping/capability mismatch, unavailable Profile/mapping or another eligible same-mapping Device. A 403 is not credential retirement.
- `409 stale_configuration`: request revision changed; refresh policy before a deliberate new request.
- `409 conversation_active`: existing Room claim; no second capture.
- `409 duplicate_claim`: claim identifier already used; never replay it.
- `503 service_unavailable`: configuration/credential/claim storage cannot establish authority.

No denied response authorizes capture. No live cutover or hardware action is authorized by this document.

## Authoritative owned paired-claim retirement

`POST /api/v1/paired-room-claims/close` uses the current own Device credential:

```json
{"schema":1,"conversation_handle":"existing-home-issued-handle"}
```

Only a persisted, known **own paired-room** row within the current credential's wake/Room/mapping scope can return:

```json
{"schema":1,"conversation_handle":"existing-home-issued-handle","admission_mode":"paired_room","status":"closed"}
```

The exact `200` receipt is idempotent for an already closed row, including after Home or bridge restart. Current credential generation is authenticated and rechecked; retiring a known older own generation creates no authority. Policy may already be removed: approval is not a prerequisite for safe owned retirement. This uses the existing notifying close operation, never opens upstream, captures audio or replays a turn. Persisted closed rows currently have no automatic deletion; an unknown row never becomes proof of retirement.

Malformed input is `400 invalid_request`; invalid/revoked identity is `401 unauthorized`; a valid credential lacking wake capability is `403 forbidden`; unknown, foreign, non-paired or out-of-scope rows are indistinguishable `404 not_found`; unavailable storage is `503 service_unavailable`. A generic stale response or `404` is **not** a confirmed close. A cold consumer record without a known mode may attempt this owned-kind lookup, but only the exact matching `200` receipt permits its existing confirmed-retirement transition; it must not delete uncertain recovery records or guess the kind.

A healthy already bound conversation may continue using its existing authoritative `conversation.close` receipt. Each new physical paired wake retires the prior logical claim before requesting a fresh grant; bounded follow-ups remain within their existing logical conversation.
