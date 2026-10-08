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

**Readiness review drafted 2026-10-08: not ready to execute.** The inventory, retirement plan, rollback runbook, gap list and owner decisions are in [validation-home-mig-09-readiness.md](validation-home-mig-09-readiness.md). The story stays `backlog`. The review is paper work only: no host, SSH, ops, device or deployment access was used, nothing in it is verified live, and every item the repository does not show is marked `UNKNOWN`.

## Proposed acceptance criteria (PROPOSED, not approved)

These make the approved Acceptance above checkable. They do not replace it, and each depends on the owner decisions named. Evidence for each is recorded as implementation, merge and physical/live acceptance separately.

- AC-1 Provenance: the deployed Home revision and wheel hash, and the Standard commit and tree state actually running, are recorded. Any difference from unmodified Standard at the chosen baseline is a named, owner-signed exception (OD-4, OD-5).
- AC-2 Inventory: every `UNKNOWN` row of the readiness inventory is resolved with an owner, by name, path and holder only; no secret contents are recorded.
- AC-3 Recovery before cutover: a compatible recovery build and the cutover backup set exist, the rollback is rehearsed including old code on the migrated database, and the result is recorded. If no compatible build exists, the rollout is paused and the surface is reported unavailable (OD-10, OD-11).
- AC-4 Gates: a per-surface matrix links exact-build, dated evidence for Epics 1, 2 and 3 (CC capability matrix). The owner records the epic status; a historical `done` does not substitute (OD-1, OD-2, OD-3).
- AC-5 Pilot: for each surface in the order of OD-12, setup, text, voice, stop, disconnect and recovery without replay, renewal and revocation (Home mode) and honest unsupported states are captured live and recorded.
- AC-6 Retirement: obsolete services are stopped, obsolete credentials invalidated and obsolete configuration removed, each listed with before and after state, time and owner (OD-8, OD-9, OD-13). Re-pairing needs are stated per client.
- AC-7 No fork or legacy pairing in a supported install: a recorded scan of code, configuration and documentation finds none, and historical evidence is kept (OD-7).
- AC-8 Household runbook: install, start/restart, upgrade and recovery for the CaticornQueen and media-server deployment, one document, with each step either exercised or labelled unexercised (OD-16).
- AC-9 History: intentional history and configuration are preserved; no automatic mode switch and no replay of an uncertain turn, before, during and after cutover.

## Proposed execution plan (PROPOSED)

Steps R0 to R8 of the readiness document, in order: record the baseline; finish the inventory; prepare and rehearse recovery; check the Epic 1-3 gates; pilot surface by surface; write the household runbook; switch the default; retire; close. Preconditions, evidence and reversibility per step are in the readiness document, sections 4 and 5. No step is approved or started.

## Open owner decisions

Questions and `PROPOSED` defaults are in the readiness document, section 7. None is decided.

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
