---
title: 'HOME-NW-17-room-device-admission — Pair a service-hosted room device with service-private credential storage'
type: 'feature'
created: '2026-10-18'
status: 'draft'
route: 'dispatch'
baseline_commit: '3fb9936'
consumer: 'hermes-relay-tui P-HOME-01 rollout (Puck bridge on ops); device-side configuration owned by hermes-relay-tui 3-P-2'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/course-correction-2026-09-23.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-home-nw-02-pairing-credentials.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-home-nw-17-client-pairing-and-direct-admission.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-home-nw-17-browser-admission.md'
---

# Implementation Spec: HOME-NW-17-room-device-admission

Draft only. No implementation, approval or deployment is claimed.

## Intent

**Problem:** The Puck's room-device endpoint is `puck_bridge` on a Linux host
(`ops`) with no platform secure store. Home cannot pair it truthfully:

- NW-02 consume returns a credential only to "an approved request with a
  ready platform secure store" (`spec-home-nw-02-pairing-credentials.md`,
  route table), and `_validate_storage` requires `platform_secure_store` for
  every type except `browser` (`src/hermes_home/domain/credentials.py:1440-1447`).
- NW-17's pairing page approves only personal-client types
  (`CLIENT_ENDPOINT_TYPES`, `credentials.py:40`) and always approves
  `client_claim` with no Room (`src/hermes_home/api/pairing.py:376-386`).
- The browser exception (`service_private_file`, NW-17-browser-admission) is
  limited to `client_claim` (`credentials.py:1450-1457`).

The course correction requires room devices to use the same Home credential
authority under their own room/wake rules
(`course-correction-2026-09-23.md`, "Replace legacy pairing with Home-owned
enrollment"). Today no route lets a service-hosted room device pair without
falsely claiming platform secure storage.

**Approach:** Let a room-device enrollment declare `service_private_file`
truthfully and be approved through the existing household approval surface,
using the existing NW-02 room-device scope (Room, `wake_claim`, wake-mapping
grant). Nothing else changes.

## Boundaries & Constraints

**Always:**
- Reuse NW-02 enrollment, approval scope, renewal, rotation and revocation
  unchanged. The admin approve route already validates Room,
  `wake_mapping_grant` and capabilities (`src/hermes_home/api/application.py:467-519`).
- Keep existing scopes and permissions: a room device gets only what the
  approver grants within NW-02 rules. `client_claim` stays limited to
  personal-client types (`credentials.py:613-616`).
- Approval stays a household action; the device cannot approve itself.
- One credential per endpoint. A room device never shares another endpoint's
  credential, including the browser appliance's.

**Never (no bypass):**
- Change wake-claim admission, arbitration, acoustic-evidence requirements,
  the touch-claim route, or claim lifecycle. A room device paired this way
  claims through the existing wake arbitration contract exactly like any
  other wake device.
- Let `service_private_file` grant extra capability, skip evidence or skip
  approval.
- Log, render or export the credential or enrollment code.

## Scope

1. Storage attestation: accept `service_private_file` for the room-device
   endpoint type(s) that Home designates for service-hosted endpoints. Keep
   `platform_secure_store` as the requirement for all other non-browser types.
2. Approval: room-device requests can be approved with Room and
   wake-mapping grant through an existing household approval surface (pairing
   page or admin route). Request and approval flows for personal clients and
   the browser stay unchanged.
3. Contract docs (`docs/contracts/v1/`) and tests for the above.

## Ownership links

- Credential mechanics (enrollment, renewal, revocation): HOME-NW-02, unchanged.
- Personal-client pairing page: HOME-NW-17, unchanged.
- Device-side application of the approved Room, credential and Wake Mapping
  on the Puck: hermes-relay-tui 3-P-2. Re-enrollment after revocation: 3-P-6.
- Fresh wake admission and its calibrated trusted proximity requirement:
  hermes-relay-tui P-4 (existing, unresolved provider). Not affected here.
- Live renewal/revocation evidence on the Puck: HOME-MIG-09 AC-5.

## Acceptance Criteria

- Given a room-device enrollment declaring `service_private_file`, when a
  household approver approves it with one Room, `wake_claim` and a
  wake-mapping grant, then consume returns a credential with exactly that
  scope.
- Given the same request asking for `client_claim`, or a non-designated type
  declaring `service_private_file`, then Home rejects it.
- Given such a credential, then its wake claims go through unchanged
  arbitration and validation; no claim route or evidence rule differs from
  other wake devices.
- Renewal, rotation and revocation behave as NW-02 specifies.
- Existing personal-client and browser pairing tests pass unchanged;
  `ruff check src tests` and `ruff format --check src tests` pass.

## Open questions

- Which endpoint type name(s) count as service-hosted room devices.
- Whether approval belongs on the NW-17 pairing page or only on the admin route.
- Whether approval should require the device to exist in household
  configuration first; claims from an unconfigured device are already denied
  `not_found`.

## Dependencies

HOME-NW-02, HOME-NW-05, HOME-NW-17. Consumed by hermes-relay-tui P-HOME-01 rollout and 3-P-2.
