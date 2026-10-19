---
id: SPEC-puck-paired-room-wake
companions:
  - admission-contract.md
sources: []
---

# Owner-approved paired-room Puck wake

## Why

Amanda approved this Home-paired Living Room Puck responding to Hey Missy without proximity qualification, accepting outside-Room hearing. Home must replace the missing calibrated-provider prerequisite only for that explicitly approved mode while retaining real pre-capture authorization and unmodified Standard Hermes as response authority. This is an owner-directed narrow change, not evidence that full voice acceptance already works.

## Capabilities

- **CAP-1**
  - **intent:** An explicitly approved paired-room Puck obtains fresh Home wake authority before capture without acoustic qualification.
  - **success:** Only the configured Device/endpoint/Room/mapping binding with active scoped credentials, current revision and sole same-mapping eligibility receives a fresh existing-lifecycle Home handle; malformed, unapproved, stale, competing or revoked requests fail closed.
- **CAP-2**
  - **intent:** Policy and authorization changes end paired authority without silently downgrading admission or replaying uncertain work.
  - **success:** Changed/removed policy or revision retires affected claims, grant resolution rechecks current authority, and the consumer rejects omission/errors/stale grants without capture or automatic strict-mode fallback.

- **CAP-3**
  - **intent:** Cold recovery retires a known own paired claim without guessing its mode or replaying uncertain work.
  - **success:** Current valid scoped Device authentication receives the exact idempotent persisted-owned-paired closed receipt after restart or policy removal. Unknown/foreign/strict/out-of-scope `404` never proves retirement; revoked identity is `401`. No upstream open or uncertain-record deletion is required.

## Constraints

- Canonical owner decision was recorded first in the private Hermes Home product hub on 2026-10-19. It renegotiates frozen P4 only for this approved paired-room mode. Existing strict wake, Touch, browser and personal-client rules stay unchanged.
- Actual intended binding is Device `c9dd86cfa9634330bf412e560969b7a9`, endpoint `ops-hermes-puck-bridge`, Room `living-room`, selected `hey-missy` → Amanda. Home configuration explicitly approves the binding; code must not hard-code its UUID as a bypass.
- At most one paired policy is configured. Any other configured, currently credential-authorized wake-enabled Device for the same mapping anywhere in the household denies paired admission. Different-mapping strict Devices remain unaffected. No proximity-free multi-device ranking is authorized.
- Home-issued fresh grants and verified current revision precede capture; credential scope/generation, revocation, Room ownership, supported recovery and no-replay remain required. No fabricated score, confidence substitute, touch workaround, browser credential reuse, fake handle or direct-Standard fallback.
- The complete configuration/request/grant/error and omission semantics are in `admission-contract.md`. Consumers must read it; mode is projected inside the existing snapshot envelope.
- Home owns NW-05 policy/grants; TUI P4/3-P-5 owns bridge identity, sessionless setup, STT, supported Standard transport and Puck TTS delivery. No new epic is introduced.
- Independent spec/code review precedes PR. No live Home/ops or hardware action occurs before an explicit fresh custody/approval handshake; both existing actors acknowledged current quiescence.
- Before compatible old-code rollback, remove the paired policy through normal revision-checked configuration and retire paired claims under the new binary. Keep the current database and credentials; otherwise forward repair. The previous binary does not understand the new field/paired authorization semantics.

## Non-goals

- Generic proximity-free wake admission, automatic approval of other Devices or mappings, multi-device ranking, strict-path changes or broad provider bypass.
- Puck firmware/audio mutations, Standard agent patches or consumer automatic renewal. Home/ops deployment is a subsequent gated action in this workstream, not excluded work; local proof does not claim live acceptance.

## Success signal

An actual isolated Home runtime issues a fresh evidence-free paired-room grant only for the approved scoped singleton and preserves normal denial/revocation/no-replay boundaries, while strict and other-surface regressions remain intact. The owning consumer subsequently proves the real authorized wake → capture → STT → Home → unmodified Standard Amanda → TTS → audible Puck journey; Home-local tests alone do not claim that physical acceptance.

## Dated implementation evidence — 2026-10-19

The approved policy above is unchanged. After independent review and both exact-source CI successes, Home `831e55e730ab49f3774ecc55a5bd111c993d2f1a` and TUI bridge `acb1da678f125e0b90672c6248b04016fa8f834c` were deployed with current state/credentials preserved. Normal revision-checked configuration **99 → 100** approved the stated existing Living Room endpoint and only `hey-missy`; actual own-credential discovery returned that eligible paired policy.

One authorized controlled Home → unmodified Standard Amanda turn observed actual 24 kHz mono/16-bit response audio, 29696 PCM bytes, normal terminal completion and confirmed claim retirement. It did not capture speech or fetch/play Puck media. [Detailed validation and current-preserving recovery](../../implementation-artifacts/validation-puck-paired-room-wake.md) distinguishes deployed source, actual probes and static installed-firmware compatibility from the still-owner-gated physical wake/STT/audibility acceptance. No other Device approval, calibrated-provider result or consumer auto-renewal is implied.
