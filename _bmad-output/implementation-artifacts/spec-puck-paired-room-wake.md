---
title: 'Puck paired-room wake — explicit Home pre-capture authority'
type: feature
created: '2026-10-19'
status: done
route: dispatch
baseline_commit: '3b3f4064004749dce7d3ee409e5bb0fc74a35be2'
context:
  - '{project-root}/_bmad-output/specs/spec-puck-paired-room-wake/SPEC.md'
  - '{project-root}/_bmad-output/specs/spec-puck-paired-room-wake/admission-contract.md'
---

<frozen-after-approval reason="Owner approved paired-room exception and parent approved conservative exact contract, 2026-10-19">

## Intent

Amanda approved this paired Living Room Puck admitting Hey Missy without proximity qualification, accepting outside-Room hearing. Deliver explicit Home pre-capture authority, not a synthetic evidence bypass. Canonical product decision was recorded first; Home NW-05 owns this policy/grant slice and TUI P4/3-P-5 owns consumer capture/voice behavior.

## Boundaries & Constraints

Always require revisioned explicit Device/endpoint/Room/mapping approval, valid scoped Device credentials, active mapping/available Profile and fresh Home handle before capture. Permit at most one paired approval. Deny if any other configured and currently credential-authorized wake-enabled Device for that same mapping exists anywhere in the household. Different-mapping strict Devices remain unchanged. Recheck policy/revision/authorization before returning a grant and while resolving paired claims; policy removal/changes invalidate affected authority. Preserve existing claim uniqueness, busy Room, generation/revocation, deadlines, recovery and no-replay.

Never hard-code a UUID bypass, invent scores/confidence, alter strict/Touch/browser/personal rules, rank proximity-free devices, reuse credentials, fabricate handles, patch Standard or start live operations. The exact wire contract is the companion `admission-contract.md`: policy inside snapshot, dedicated paired-room-claims route, evidence-free wake-word initiation, existing grant envelope plus explicit mode. Consumer rejects omission/errors/stale grants; no silent fallback.

## I/O & Edge-Case Matrix

| Scenario | Expected result |
| --- | --- |
| Exact approved singleton/current revision | Fresh Home-issued paired grant; no acoustic fields |
| Missing policy, mismatched identity/scope or competitor for same mapping | 403; no claim/capture |
| Unrelated mapping strict Device | Paired request remains eligible; strict route unchanged |
| Malformed body/evidence/unknown mode | 400; no authority |
| Stale revision / duplicate / busy Room | 409 with existing bounded code |
| Invalid/revoked credentials | 401 and existing active-work revocation |
| Policy/configuration/eligibility changes | Paired claims cannot remain authorized |
| Configuration/storage unavailable | 503; no fallback |
| Known own paired claim, including closed/restarted/policy removed | Exact idempotent owned `200 closed` receipt; no upstream/replay |
| Unknown/foreign/strict/out-of-scope retirement handle | Indistinguishable 404; never retirement proof |

</frozen-after-approval>

## Code Map

- `domain/configuration.py`, `docs/contracts/v1/configuration.schema.json`: optional validated Device approval, defaults unchanged; at most one policy.
- `domain/credentials.py`: expose read-only current active wake authorization contexts, never secret material.
- New `domain/paired_room.py`: pure explicit policy/scope/exclusivity and request validation; no acoustic score or strict-arbitration changes.
- `api/application.py`: authenticated discovery/new route, revision rechecks and existing claim creation/retirement/error envelope; configuration publish invalidates paired claims.
- `domain/arbitration.py`: extend decision kind and atomically reserve IDs in the existing accepted-ID set; strict submission/scoring remains unchanged.
- `bridge/production.py`, `domain/conversations.py`: persist distinct paired claim kind, recheck current authority during resolution and retire known own scoped paired rows idempotently.
- `tests/test_paired_room_policy.py`, `test_paired_room_claim_store.py`, `test_paired_room_api.py`: plausible consumer regressions, durable boundaries and production-runtime HTTP proof.

## Tasks & Acceptance

- [x] Add validated optional policy/schema and read-only current authorization view.
- [x] Add evidence-free singleton policy/grant path and exact discovery projection.
- [x] Preserve dynamic revocation/revision authority for durable paired claims and strict behavior.
- [x] Add boundary, compatibility, race and current-runtime HTTP coverage.
- [x] Add exact owned paired retirement and cold same-SQLite/root recovery; unknown kinds stay uncertain.
- [x] After smoke, update contract/runbook/changelog and existing ownership/status/evidence records.
- [x] Complete independent spec/code review before PR; no self-merge or ungated live mutation.

Given explicit exact approval and sole same-mapping eligibility, when the own credential requests current-revision wake initiation, then Home returns a fresh correctly bound grant before capture. Given any boundary violation or policy/authorization change, when requesting/resolving the claim, then Home fails closed and does not replay work. Given existing strict/other-surface contracts, when regressions run, then they remain unchanged. Actual isolated HTTP smoke must exercise production construction/storage/routes rather than only mocks.

## Implementation Notes

Parent resolved conservative household same-mapping exclusivity; no open intent question remains. Identity owner acknowledged exact wire fields and is implementing consumer separately. Both prior host/hardware actors explicitly acknowledged quiescence; no new live approval is implied.

Parent explicitly approved narrow owned paired close after source/consumer proof that generic stale reconnect cannot establish authoritative retirement. Canonical product hub and shared contract record exact receipt and 404 non-proof; this is not a generic lifecycle redesign. Current Device authentication/generation is rechecked, policy removal does not prevent safe retirement, and known older own row generations may retire without creating authority.

Integrated local proof: 137 new focused tests passed; standalone production load_settings/create_runtime HTTP smoke passed across same SQLite/root reopen. Independent actual TUI factory + HomeApplication HTTP smoke passed seven scenarios. Standard/audio/hardware/live hosts were unexercised at that local checkpoint. API wires the current-authority checker; no runtime behavior change was necessary.

Final review found one real revocation-publication defect: resolution retired paired rows silently, dropping a live Standard bridge's callback. The smallest patch reuses existing notifying retirement outside the store lock, retaining deny-before-storage and strict semantics. Live bridge regressions prove same-revision competitor/revision denial interrupts and tears down the binding, with no duplicate owned-close notification. The original independent reviewer confirmed this patch resolves the finding. Final local verification is 143 focused / 1499 full passing; see [validation](validation-puck-paired-room-wake.md) for the observed warning and physical/live gates.

## Spec Change Log

## Review Triage Log

| Finding | Verdict / route | Evidence and resolution |
| --- | --- | --- |
| Specialist initial claim that `InMemoryConversationClaimStore.resolve` ignores its checker | false / reject | No such resolver or bridge grant-resolution path exists in that in-memory test port; actual production resolution is `ConversationGrantStore.resolve`. No dead resolver was added. |
| Specialist repeated same in-memory claim | false / reject | Carried: unchanged nonexistent location/caller claim; source class and all resolver consumers disprove it. Count-driven output was not accepted as evidence. |
| Consolidated full-capability review: silent paired retirement loses live revocation notification | high / patch | `_close_locked` removed registered handlers; Standard reauthorization only returned stale while active upstream remained bound. Existing notifying closure now runs outside resolution lock; parametrized live bridge tests prove interrupt, teardown and single notification. Original reviewer independently confirmed fixed. |

Parent directed one context-free full-capability review covering blind, edge and verification lenses rather than manufactured finding counts. It found the notification defect above; focused patch review found no remaining issue. Firmware actor independently reviewed full spec/code/recovery and found no additional substantiated issue. Its read-only review did not rerun our checks or authorize deployment.

## Verification

Supported Python 3.14: focused paired/configuration/credential/production suites, `ruff check src tests`, `ruff format --check src tests`; full suite once after integration. Standalone isolated production runtime HTTP approval/configuration/claim/revocation smoke with temporary secrets/state and no upstream/audio/hardware. Independent spec and code review before PR.

Observed integrated verification: Python 3.14.8, 137 focused tests passed in 1.49s; full suite 1493 passed/26 skipped/7 existing WebSocket deprecation warnings in 30.53s (artifact 3424). Ruff check passed and format check reported 87 files already formatted. The standalone production runtime HTTP smoke and independent actual TUI HTTP factory smoke passed; these isolated checks did not establish live/upstream/audio success. Later dated deployment and owner acceptance are recorded below.

Final post-review verification: 143 focused passed in 1.76s; full 1499 passed, 26 skipped, 8 warnings in 29.24s (artifact 3515). Seven warnings were the known WebSocket deprecations; one was a startup/close `Bad file descriptor` thread warning in existing `test_create_runtime_recovers_paired_credentials_after_restart` (runtime and that test unchanged). It was recorded, not suppressed or rerun away. Ruff check and format check passed (87 files). Standalone production HTTP/reopen smoke and exact old-4c wheel/DB compatibility rehearsal both passed after the fix. These local checks did not exercise live Standard/audio/physical acceptance.

Owner-directed integration: merged `origin/main` at `f6483c7` (PR110 recovery/evidence) normally, preserving both additive changelog entries. Runtime/tests are byte-identical to reviewed `2a2ab04`; independent runtime review remains valid. Required post-merge verification: 143 focused / 1499 full passing, 26 skipped, 7 known deprecations, Ruff pass/87 formatted (artifact 3561). Both replacement Home/TUI exact-source CI successes gated deployment; old source/artifacts were superseded. No live operation had begun at this pre-deployment checkpoint.

Subsequent parent-approved execution completed Home/ops steps after actual CI successes: Home `831e55e...` and initial TUI `acb1da6...` deployed, original Home credentials/enrollment and browser preserved, persistent private bridge active, token auth boundaries proved, and exact Living Room paired policy published by normal API **99 → 100**. The controlled Standard Amanda speech probe passed with 24000 Hz mono/2-byte PCM, 29696 bytes and confirmed closed/retired claim. This was the pre-physical readiness checkpoint, not a claim that the probe established speech capture/audibility.

Physical acceptance on 2026-10-19: the first window found real faster-whisper/PyAV 19 `metadata_errors` incompatibility before decode/VAD/inference. After independently reviewed TUI `6b288939...`/PyAV 18.1.0 exact-source CI, targeted isolated-venv correction, real on-host synthetic decode and 401/404 checks, the second parent-GO window completed actual wake → Home grant before capture → STT → unmodified Standard Amanda → TTS → Puck playback. **Amanda explicitly confirmed a reasonable audible answer.** Primary capture was 388960 bytes; 112 audio chunks delivered 685568 PCM bytes completely. Exact owned close 200 and retired recovery left **0 total active Home claims**. Home/config/firmware/volume remained unchanged during the bridge correction. Nonzero speaker/follow-up warnings and unaccepted edge matrices are detailed in validation; no broader provider/renewal/story closure is inferred. Both observers/custodians are quiescent; no further live actions.
