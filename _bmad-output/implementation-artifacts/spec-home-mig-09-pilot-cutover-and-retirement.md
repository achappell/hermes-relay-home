---
id: HOME-MIG-09
status: backlog
product_epic: 4
created: 2026-09-23
github_issue: https://github.com/achappell/hermes-relay-home/issues/55
validation: _bmad-output/implementation-artifacts/validation-home-mig-09-readiness.md
---

# HOME-MIG-09 — Run the household pilot and retire legacy dependencies

## Approved scope

Reuse migration source Story 9; inventory and retire fork paths, services and credentials only after required surface evidence. Provide repeatable actual-household install, upgrade and compatible recovery. Record deployed upstream provenance. Broader NW-15 packaging remains later scope.

## Acceptance

- Deliver the owner-specific behavior above using unmodified Standard Hermes and the approved [delivery contract](course-correction-2026-09-23.md).
- Preserve existing story evidence and supported adapters; no automatic mode switch or replay of an uncertain turn.
- Record applicable setup, capability limits, privacy, failure and recovery behavior against the actual supported baseline.
- Record implementation, merge and physical/live acceptance separately; do not declare an unexercised gate complete.

## Dependencies

- epic:1
- epic:2
- epic:3

## Readiness

Approved backlog scope. Owning BMAD specification/readiness review must settle API details and a bounded execution plan before implementation. No implementation or runtime acceptance is claimed.

**Readiness refreshed 2026-10-27: not ready for cutover.** [Current evidence and bounded plan](validation-home-mig-09-readiness.md#0-current-readiness--2026-10-27) supersede the preserved October 8 paper snapshot. Read-only host/configuration/installed-source checks partially establish R0/R1; newer records establish revision-bound recovery rehearsals, not a generic production rollback guarantee. Required clean Standard provenance, per-surface migration acceptance, Apple direct-mode implementation, signed macOS distribution and next-candidate recovery remain open. Puck physical validation is deferred, not waived. Status remains `backlog`; no deployment, credential invalidation, service retirement or epic closure is authorized.

## Proposed acceptance criteria (PROPOSED, not approved)

These make the approved Acceptance above checkable. They do not replace it, and each depends on the owner decisions named. Evidence for each is recorded as implementation, merge and physical/live acceptance separately.

- AC-1 Provenance: the deployed Home revision and wheel hash, and the Standard commit and tree state actually running, are recorded. The selected supported Standard baseline satisfies the approved unmodified-agent contract; a named patch or owner-signed receipt alone does not satisfy it. Any exception requires a separate explicit product-contract decision before acceptance (OD-4, OD-5).
- AC-2 Inventory: every `UNKNOWN` row of the readiness inventory is resolved with an owner, by name, path and holder only; no secret contents are recorded.
- AC-3 Recovery before cutover: a compatible recovery build and the cutover backup set exist, the rollback is rehearsed including old code on the migrated database, and the result is recorded. If no compatible build exists, the rollout is paused and the surface is reported unavailable (OD-10, OD-11).
- AC-4 Gates: a per-surface matrix links exact-build, dated evidence for Epics 1, 2 and 3 (CC capability matrix). The owner records the epic status; a historical `done` does not substitute (OD-1, OD-2, OD-3).
- AC-5 Pilot: for each surface in the order of OD-12, setup, text, voice, stop, disconnect and recovery without replay, renewal and revocation (Home mode) and honest unsupported states are captured live and recorded.
- AC-6 Retirement: obsolete services are stopped, obsolete credentials invalidated and obsolete configuration removed, each listed with before and after state, time and owner (OD-8, OD-9, OD-13). Re-pairing needs are stated per client.
- AC-7 No fork or legacy pairing in a supported install: a recorded scan of code, configuration and documentation finds none, and historical evidence is kept (OD-7).
- AC-8 Household runbook: install, start/restart, upgrade and recovery for the CaticornQueen and media-server deployment, one document, with each step either exercised or labelled unexercised (OD-16).
- AC-9 History: intentional history and configuration are preserved; no automatic mode switch and no replay of an uncertain turn, before, during and after cutover.

## Proposed execution plan (PROPOSED)

Retain steps R0–R8: provenance/inventory; compatible recovery; required per-surface gates; controlled pilot; actual-household runbook; approved default switch/bake; retirement; closure. The current readiness document section 0 supplies owners, exit evidence and stop conditions. Work follows Home-personal, direct-personal, room-device and retirement dependency order. Only bounded read-only R0/R1 observations were performed in this audit; no mutation/pilot/cutover/retirement stage was executed or newly approved. Historical sections 4–5 are not a current executable runbook.

## Open owner decisions

The original question identities below are retained for traceability. Current readiness section 0 distinguishes requirements already settled by the delivery contract, newer scoped approvals/rehearsals, and remaining owner decisions. This audit chooses no new upstream baseline, patch exception, scope reduction, pilot/bake duration, credential conversion or retention policy.

| ID | Question |
| --- | --- |
| OD-1 | What is the Epic 2 gate for Home, given no Home story or tracker key exists? |
| OD-2 | What evidence moves `epic-1` and `epic-3` out of `in-progress`? |
| OD-3 | Are custom wake phrases (HOME-NW-13) in the migration release? |
| OD-4 | Which Standard commit is the supported baseline (pin `2237be3` versus the media-server checkout)? |
| OD-5 | How is the live speak-stream patch treated against "unmodified Standard"? |
| OD-6 | Do the pilot relay proxy and pilot Standard service stay as supported components? |
| OD-7 | Is the static device-credentials mode retired in code? |
| OD-8 | What does retiring the fork mean on the agent host? |
| OD-9 | Re-pair or convert credentials, and which are invalidated? |
| OD-10 | What is the compatible recovery build and how long is it kept? |
| OD-11 | Where is rollback rehearsed? |
| OD-12 | Pilot order, duration, bake window and rollback authority? |
| OD-13 | What happens to host residues (grants file, legacy logs, old backups)? |
| OD-14 | Does NW-06 diagnostics gate the pilot? |
| OD-15 | Where is the signed macOS build tracked? |
| OD-16 | Scope of the household runbook? |

## Spec Change Log

- 2026-10-08: Added the readiness review, proposed acceptance criteria, proposed execution plan and open owner decisions (documentation only). Status unchanged (`backlog`). Added the `github_issue` and `validation` pointers, matching `story-index.yaml`.
- 2026-10-27: Refreshed readiness with current repository evidence, bounded read-only Home/media-server/ops observations, installed-source/RECORD comparisons, per-platform/mode acceptance ledger, revision-bound recovery caveats and staged R0–R8 exit gates. Clarified that the firm unmodified-Standard contract is not satisfied by merely naming a local patch. Preserved the historical audit and all source/credential/service state; status remains `backlog`.
- 2026-10-27: Evaluated exact clean upstream e5ea5fd (also live disk base) in isolation; real loopback provider and offline Slack gates confirm missing timeout/no-retry and exact-peer behavior. Preserved/reconstructed source-only recovery material, five files matching. Candidate rejected for cutover; no upstream patch/config workaround or production change. See readiness section 0.1 for source anchors, smoke evidence, artifact hashes and unresolved upstream prerequisite.
