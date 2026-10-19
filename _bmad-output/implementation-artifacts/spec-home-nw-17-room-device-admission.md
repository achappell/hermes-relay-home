---
title: 'HOME-NW-17-room-device-admission — Pair a service-hosted Puck bridge as a fixed Room device'
type: 'feature'
created: '2026-10-18'
status: 'draft'
route: 'dispatch'
baseline_commit: '3fb9936'
consumer: 'hermes-relay-tui P-HOME-01 (Puck bridge on ops)'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/course-correction-2026-09-23.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-home-nw-17-client-pairing-and-direct-admission.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-home-nw-17-browser-admission.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-home-nw-02-pairing-credentials.md'
---

# Implementation Spec: HOME-NW-17-room-device-admission

Planning only. Nothing here is implemented, deployed or approved. Decisions
marked **[OWNER]** need Amanda / the Home owner before `ready-for-dev`.

## Intent

**Problem:** The reSpeaker Puck must run permanently through Home. Its
TUI-side bridge (`puck_bridge`) runs as a systemd service on `ops` and holds
the Puck's Device credential in a 0600 service file. Home today cannot pair
that endpoint from its own pairing surface:

- `CLIENT_ENDPOINT_TYPES` is `tui, ios, macos, android, browser`
  (`src/hermes_home/domain/credentials.py:40`); the pairing page approve route
  rejects every other type with `unsupported_endpoint_type`
  (`src/hermes_home/api/pairing.py:376-377`) and always approves
  `rooms=(), capabilities=("client_claim",)` (`pairing.py:383-386`).
- `_validate_storage` demands `service_private_file` only for `browser` and
  `platform_secure_store` for every other type (`credentials.py:1440-1447`).
  A headless Linux service has no platform secure store; claiming one would be
  a false attestation.
- The admin route `POST /api/v1/enrollment/requests/{id}/approve` already
  accepts `rooms`, `capabilities`, `wake_mapping_grant`, `touch_binding`
  (`src/hermes_home/api/application.py:467-519`), so the credential *scope*
  for a Room wake device exists; only the endpoint attestation and the
  household-facing approval path are missing.
- Credentials expire after 90 days with a 14-day renewal window
  (`credentials.py:34-35, 800-906`). A permanent device must renew.
- Home restart closes every active claim with `service_restart`
  (`src/hermes_home/bridge/production.py:139-140`), and a closed handle cannot
  be reopened. Every Home restart therefore forces the device to claim again
  through wake arbitration.

Arbitration already accepts a device-local scalar
(`acoustic_evidence.value`, `src/hermes_home/domain/arbitration.py:315-354`);
Home does not require calibration. The "calibration" gate that blocks the Puck
is bridge-side (TUI P-HOME-01), not Home-side.

**Approach:** Add one Room-device endpoint type for a service-hosted wake
device, attested as `service_private_file`, pairable from the Home pairing page
with an explicit Room, `wake_claim`, wake-mapping grant and priority, and
renewable without re-pairing. No new admission semantics: the device claims
through existing Room arbitration on every fresh wake.

## Boundaries & Constraints

**Always:**
- One credential per physical Puck; never shared with the browser appliance's
  `pairing.json` or any other endpoint.
- Room-device scope: exactly one Room, `wake_claim` only (no `client_claim`,
  `touch_claim`, `sensitive_entry`, `consequence_confirm`, `health_view`)
  unless the owner grants more later through the existing admin route.
- Approval is a household action on the pairing page (or admin API); the
  device itself cannot approve.
- The device must also exist in household configuration (Room, priority)
  before claims succeed; otherwise arbitration denies with `not_found`.
- Revocation and rotation interrupt live bridge connections as today
  (`credentials.py:1575+` revocation observer).

**Never:**
- Treat the room device as a personal client (no `client_claim`, no
  Profile picker, no idle-timeout client semantics).
- Persist claims across Home restart or reopen a closed handle.
- Log, render or export the credential or enrollment code.
- Change arbitration scoring, window, or denial reasons.

## Design

1. **Endpoint type** `room_service` **[OWNER: name — `puck`, `room_service`
   or `room_device`]**: a new member of a `ROOM_SERVICE_ENDPOINT_TYPES`
   set. `_validate_storage` accepts `service_private_file` for it (and for
   `browser`). `_validate_room_service_scope` permits only `wake_claim`
   (optionally `touch_claim` for future Touch use — **[OWNER]**).
2. **Pairing page:** the request list shows room-service requests separately
   ("Room device"), and approval collects Room, wake-mapping grant (one
   mapping by default) and confirms the device entry exists in configuration;
   it calls the same `approve_request` the admin route uses. No Profile
   picker.
3. **Enrollment request shape** (TUI pairs against this):
   `type=<room type>`, `requested_rooms=[<room>]`,
   `requested_capabilities=["wake_claim"]`,
   `secure_storage="service_private_file"`. Consume returns `device_id`,
   `credential`, `generation`, `expires_at`, grants as for clients.
4. **Renewal:** existing `POST /api/v1/devices/{id}/credentials/renew`
   works for the new type (verify; add tests). No new endpoint.
5. **Restart contract (documented, not changed):** after Home restart all
   claims close `service_restart`; the device must submit a fresh wake claim.
   With one Puck in the Room the claim wins arbitration uncontested; with
   several devices the configured priority breaks equal scores.

### Admission policy for a fixed-location device — options

Home already trusts the device-local score. The policy question is what the
bridge may claim when the Puck sends no score (firmware uploads only audio +
`X-Puck-Token`):

| Option | Mechanism | Security tradeoff |
|---|---|---|
| A (recommended) | Bridge submits a **configured fixed score** for its single paired device on every Puck wake upload; Home unchanged except a per-device `evidence_source: "fixed"` flag recorded in the device configuration and diagnostics | Evidence no longer proves proximity. Anyone who can POST `/upload` with the Puck token on the LAN can start a conversation in that Room's mapped Profile. Bounded by: LAN-only bind, per-device token, single Room, `wake_claim` only, revocation. Equal fixed scores across devices fall to configured priority |
| B | Home-side "trusted fixed device" policy: Room devices flagged `fixed_location: true` skip score comparison and win only by priority | Same exposure as A, plus an arbitration change in Home; larger blast radius |
| C | Firmware reports microWakeWord probability; bridge forwards it with a fixed calibration ID | Most faithful; firmware change + P-7 re-test; still not proof of presence. Deferred enhancement |

**[OWNER] D-H1:** accept Option A's exposure for the Puck Room, or require C
before production.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error |
|---|---|---|---|
| Pair Puck | Room-service request, `service_private_file`, `wake_claim`, one Room | Pending; owner approves with Room + mapping; consume returns credential | Wrong storage → `400`; `client_claim` requested → `400` |
| Pairing page, client type | Existing flows | Unchanged | — |
| Approve without config device | Device not in configuration | Approval succeeds; claim denied `not_found` until configured — **[OWNER] D-H2: block approval instead?** | — |
| Home restart | Active Puck claim | Closed `service_restart`; next wake claims fresh | — |
| Renewal window | <14 days left | Renew returns generation+1; old overlaps 600 s | Expired → re-pair |
| Revoke | Admin revokes | Live bridge socket closed; claims `401/403` | Bridge reports `identity_rejected` |

## Code Map

- `src/hermes_home/domain/credentials.py`: endpoint-type sets, `_validate_storage`, scope validation for room-service type.
- `src/hermes_home/api/pairing.py`, `src/hermes_home/api/pairing_page.py`: room-device approval path (Room, mapping), no Profile picker.
- `src/hermes_home/api/application.py`: admin approve already supports scope; add type validation only.
- `docs/contracts/v1/`: enrollment request type/storage for room-service devices; restart contract note.
- `tests/`: pairing, storage attestation, scope rejection, renewal, revocation, restart-closes-claim for the new type.

## Tasks & Acceptance

**Execution:**
- [ ] Owner decisions D-H1…D-H4 recorded in this spec.
- [ ] Endpoint type, storage attestation, scope validation.
- [ ] Pairing page approval for room devices.
- [ ] Contract docs + tests listed above.
- [ ] Validation record `validation-home-nw-17-room-device-admission.md`.

**Acceptance Criteria:**
- Given a room-service enrollment with `service_private_file` and `wake_claim`, when the owner approves it with one Room and one wake mapping on the pairing page, then consume returns a Device credential whose scope is exactly that Room, `wake_claim`, and that mapping.
- Given the same request asking for `client_claim` or claiming `platform_secure_store`, then Home rejects it.
- Given an approved Puck device present in configuration, when the bridge submits a wake claim with a valid `opaque-v1` value, then arbitration grants and returns a handle; after a Home restart the old handle is closed and a fresh claim succeeds.
- Given 13 days to expiry, renewal succeeds without re-pairing; after revocation the live bridge socket is closed.
- Existing tui/ios/macos/android/browser pairing tests pass unchanged; `ruff check src tests` and `ruff format --check src tests` pass.

## Owner decisions

- **D-H1** Fixed-score admission (Option A) acceptable for the Puck Room, or firmware-reported score (C) required first.
- **D-H2** Block approval when the device is absent from configuration, or allow and deny claims `not_found`.
- **D-H3** Endpoint type name and whether `touch_claim` is allowed for the type.
- **D-H4** Which Room and wake mapping/Profile the Puck gets; its priority relative to other Room devices.

## Dependencies

HOME-NW-02, HOME-NW-05, HOME-NW-17. Unblocks hermes-relay-tui `P-HOME-01`.
