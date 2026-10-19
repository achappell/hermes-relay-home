---
title: 'HOME-NW-17-room-device-admission — Pair a service-hosted Puck with private credential storage'
type: 'feature'
created: '2026-10-18'
updated: '2026-10-19'
status: 'done'
route: 'dispatch'
baseline_commit: 'b2f31c6355271b057201ce02c1ff8b39ca90e56c'
consumer: 'hermes-relay-tui P-HOME-01 ops bridge; device-side application owned by 3-P-2'
context: []
---

# Implementation Spec: HOME-NW-17-room-device-admission

<frozen-after-approval reason="Owner-approved pairing scope, 2026-10-19">

## Intent

**Problem:** The Linux-hosted Puck bridge cannot truthfully declare a platform secure store. Home currently permits private-file storage only for browser appliances, whose client-only grants cannot authorize a room device.

**Approach:** Extend existing `puck` enrollment with truthful `service_private_file` attestation and tightly bounded room/wake approval through the existing authenticated admin API. Deliver actual approved enrollment and existing credential lifecycle over local HTTP, not just a storage flag. The owner approved this scope and the contract decisions below on 2026-10-19.

## Boundaries & Constraints

**Always:** Reuse NW-02 offers, confirmation proof, approval, consume, renewal, rotation and revocation. Only private-file `puck` requests require exactly one Room and exactly `wake_claim`; approval additionally requires nonempty active Wake Mapping IDs with available Profiles. The approved Room must be requested and configured. Preserve existing platform-store `puck`, browser, personal-client and Touch behavior. Home validates the storage declaration, not the remote filesystem; the consumer enforces private directory/file ownership and permissions. Request and consume attestations must match.

Use the existing `puck` type, not service-module name `puck_bridge`: NW-02 and permanent tests already use `puck`. Storage-specific validation avoids a second identity convention or consumer migration. Admin-API-only approval is intentional: `/pair` already directs room devices there and remains personal-client-only. Authenticate as the household administrator, inspect redacted identity/confirmation/requested scope, and explicitly select Room/mappings. The Puck cannot approve itself.

Do not require a preconfigured Device: consume generates its `device_id`. Configure Room/mappings first, approve and consume, then add that exact generated Device ID through revision-checked configuration publishing. Claims remain denied until configured; no database edits or guessed mappings.

**Never:** Change wake admission, arbitration, calibrated proximity/evidence, touch admission or claim lifecycle. Add capabilities, `client_claim`, Touch binding, browser credential reuse, Standard tokens, fake acoustic scores or fallback. Print credentials or copy them into logs, units, argv, browser storage or the repository. Preserve existing owner-facing enrollment offer/QR/code and confirmation proof; only their legitimate offer/request responses expose those proof materials.

## I/O & Edge-Case Matrix

| Scenario | Input / state | Expected behavior |
| --- | --- | --- |
| Private Puck | `puck`, private file, one Room, wake only | Pending request; no credential before admin approval |
| Bad request | Extra capability, zero/multiple Rooms, unrelated type with private file | Rejected without pending state |
| Bad approval | Wrong/unconfigured Room, missing/inactive/unavailable mapping, extra capability/binding | Rejected; request stays pending |
| Approved consume | Same attestation and offer proof | One credential, exact approved Room/mappings, no client grants |
| Changed proof/storage | Wrong code or changed attestation/type | No credential; original request remains usable |
| Lifecycle | Renewal window, rotation, revoke/re-enroll | Existing NW-02 generation/retry/revocation rules |
| Configuration | Issued but unconfigured Device | No claim; revision-checked publish enables normal configuration discovery |
| Compatibility | Existing endpoint/storage contracts | Existing behavior unchanged |

</frozen-after-approval>

## Code Map

- `src/hermes_home/domain/credentials.py`: `_validate_storage`, request/approval validation. Reuse persisted request storage/type and `CredentialScope`; no schema migration.
- `src/hermes_home/api/application.py`: existing admin approval validates configuration and explicit `wake_mapping_grant`; consume and revision-checked configuration routes already suffice. Do not change claim routes.
- `src/hermes_home/api/pairing.py`, `pairing_page.py`: keep personal-client approval and room-admin guidance unchanged.
- `tests/test_credentials.py`, `test_credentials_api.py`: NW-02 SQLite/lifecycle helpers; `test_browser_admission.py` demonstrates real loopback HTTP adapter coverage.
- `docs/contracts/v1/README.md`: scope/storage and operator sequence; `CHANGELOG.md`: user-visible change.

## Tasks & Acceptance

**Execution:**
- [x] `src/hermes_home/domain/credentials.py` — add private-file Puck validation at submit/approve/consume without changing other endpoint behavior.
- [x] `tests/test_room_device_enrollment.py` — permanent boundary and real HTTP lifecycle/configuration coverage following existing test conventions.
- [x] `docs/contracts/v1/README.md`, `CHANGELOG.md` — document exact type/storage/scope, authenticated operator flow and consumer obligations.
- [x] Existing story index/sprint status and `validation-home-nw-17-room-device-admission.md` — record observed local evidence and exact live dependencies.

**Acceptance Criteria:**
- Given private-file Puck enrollment, when the administrator approves one requested configured Room and nonempty available mapping grant, then consume returns exactly that authority once.
- Given malformed or broadened request/approval or changed attestation, when submitted, then no credential or broadened authority is issued.
- Given an issued Puck credential, when unconfigured/revoked/old, then existing configuration/auth/claim boundaries deny it appropriately; renewal/rotation/re-enrollment retain NW-02 semantics.
- Given existing personal/browser/Touch/platform-store Puck behavior, when regression suites run, then it is unchanged.
- Given temporary isolated Home state, when real HTTP enrollment through revocation runs, then responses prove the complete local flow without live credentials or hardware interaction.

## Implementation Notes

Independent contract recon confirmed `puck` naming, admin route, generated identity and local HTTP precedent. Live pairing requires reviewed Home deployment plus an approved real Room/mapping and the consumer's private credential installation; do not infer automatic bridge renewal from Home lifecycle tests. P4 calibrated provider and first-connect recovery remain separate blockers.

- Local verification: 179 focused tests; full suite 1,352 passed, 26 skipped, 7 existing WebSocket deprecation warnings; Ruff check/format-check passed. Separate production-runtime HTTP smoke passed with temporary paired SQLite, mode-0600 credential output, revision-checked Device publication and lifecycle/revocation checks.
- Consume does not accept a `type` field: its strict schema rejects that unsupported field. The HTTP test verifies rejection, not mutation of stored type. Endpoint type selection and storage-specific scope are validated when requesting/approving enrollment.
- Independent live read-only reconnaissance found revision 97, sole Room `kitchen`, sole active mapping `hey-missy` targeting `amanda`, and no configured Puck. This is configuration evidence, not current deployed-build or safe-recovery evidence; parent-owned host provenance/recovery checks are still in progress.
- Review patches changed tests only: 34 room-enrollment cases passed after adding configured denial, mapping-expansion snapshot/empty, platform multi-Room compatibility, complete issued-secret redaction and SQLite reopen coverage. No production abstraction or admission changes were needed.
- Final independent verification after patches: 183 focused tests passed; full suite 1,356 passed, 26 skipped, 7 existing warnings; Ruff check and format-check passed. A second independent production-runtime HTTP enrollment/configuration/revocation smoke exited 0. Parent signed off publication; this completes the local Home implementation, not live deployment or Puck consumer rollout.
- Custodian raw SSH/PowerShell read-only access succeeded. Live package provenance is mixed (four source files differ from its install manifest), so deployment needs fresh current-preserving package/online-SQLite backup and compatible recovery. Existing Kitchen/hey-missy values are not physical-Puck intent: owner Room/mapping assignment is still required before live enrollment. P4 remains a runtime gate, not a credential-enrollment prerequisite; no merge is authorized.

## Spec Change Log

- 2026-10-19: Resolved draft questions using existing NW-02 contracts and independent recon; owner approved `puck` storage-specific narrowing, admin API approval and postconsume configuration. Preserved existing platform-store behavior and enrollment proof presentation.

## Review Triage Log

All three required independent layers completed before triage: blind hunter, edge-case hunter (no findings) and verification-gap reviewer (no gaps).

| Finding | Verdict / route | Evidence and resolution |
| --- | --- | --- |
| Blind 1: HTTP fixture is not production runtime evidence | false | Correct distinction, but separate standalone `load_settings`/`create_runtime` real HTTP exercise already passed; the subsequently written validation artifact records it without claiming the fixture proves startup. Include that artifact in the final diff. |
| Blind 2: Configured private-Puck wake denials untested | medium / patch | Existing HTTP case stopped at unconfigured denial. Extended it to assert unauthorized mapping, out-of-grant configured Room and missing-evidence denials through unchanged admission. Targeted suite passes. |
| Blind 3: Private-file `all_current_profiles` boundaries untested | medium / patch | Added nonempty approval snapshot and exclusion of a later mapping, plus empty expansion rejection retaining pending state. Targeted suite passes. |
| Blind 4: Broader platform-Puck compatibility untested | medium / patch | Added multi-Room platform-store request/approval/consume counterexample; private-file multi-Room request remains rejected. Targeted suite passes. |
| Blind 5: Final/browser issued secrets omitted from redaction check | low / patch | Extended existing checked set to include re-enrollment and browser proofs/credentials as well as every Puck generation. Targeted suite passes. |
| Blind 6: Private request persistence/reopen untested | medium / patch | Added approved-request close/reopen on identical SQLite/root secret; recovered exact scope/type, rejected changed storage, then consumed with original attestation. Targeted suite passes. |
| Blind 7: Validation artifact absent from review snapshot | false | Artifact was created after that snapshot and exists with exercised commands, runtime smoke and deployment boundaries. It is included in the final reviewed change, not left as a dangling link. |

Independent specification reconnaissance supplied pre-review context. Operator/admin-local and valid Device-record details are documented in the contract; unsupported consume `type` is clarified in Implementation Notes. Consumer automatic renewal, private-file application, calibrated proximity and first-connect recovery remain separate, not implied complete by Home tests.

## Verification

Use supported Python 3.14: focused room enrollment and existing credential/browser/pairing suites, `ruff check src tests`, `ruff format --check src tests`. Also exercise the production runtime over real local HTTP with isolated temporary secrets/state; never print secret response bodies. Independent spec+code review and parent signoff precede PR or live deployment.
