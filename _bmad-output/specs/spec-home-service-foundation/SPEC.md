---
id: SPEC-home-service-foundation
updated: 2026-09-12
companions:
  - ../../planning-artifacts/architecture/architecture-hermes-relay-home-2026-09-12/ARCHITECTURE-SPINE.md
  - ../../../docs/contracts/v1/README.md
  - ../../../docs/contracts/v1/configuration.schema.json
  - ../../../docs/contracts/v1/wake-claim.schema.json
sources:
  - ../../../docs/project-context.md
  - ../../../docs/architecture.md
---

> **Canonical contract.** This SPEC and the files in `companions:` are the
> complete contract for the first Home service foundation slice. The source
> documents are retained for traceability.

# Home Service Foundation

## Why

This is a boundary and coordination problem: iOS, Android, the TUI, web/iPad,
and physical wake devices need one local household authority before any of them
can safely publish shared configuration or resolve a simultaneous wake. The
foundation makes that authority durable, authenticated, deterministic, and
small enough to validate before a full HTTP surface is built.

## Capabilities

- **CAP-1**
  - **intent:** An authenticated client can read the active household
    configuration from the Home service.
  - **success:** A read returns the versioned envelope and the last committed
    snapshot revision from the durable store.

- **CAP-2**
  - **intent:** An authenticated administrator can replace the complete
    household configuration without losing a concurrent update.
  - **success:** A valid expected revision activates one complete snapshot and
    increments the revision; a stale revision returns `revision_conflict` and
    leaves the active snapshot unchanged.

- **CAP-3**
  - **intent:** An authenticated wake-capable Device can submit a claim and
    receive its bounded grant or denial decision.
  - **success:** Deterministic fixtures prove eligibility, the 250 ms
    arbitration window, proximity comparison, per-Room priority tie-breaking,
    and claim-specific outcomes.

- **CAP-4**
  - **intent:** The service fails closed when a claim is malformed, late,
    unavailable, revoked, or not mapped to the active configuration.
  - **success:** Invalid identity and invalid claims produce safe typed errors or
    denial, with no audio, transcript, prompt, or Hermes turn created.

- **CAP-5**
  - **intent:** The service recovers its canonical configuration after a process
    restart without creating a second state authority.
  - **success:** A restart/reopen test reads the same last committed revision;
    rejected writes and failed claims do not mutate durable configuration.

## Constraints

- The existing v1 HTTP contract and JSON Schemas are the wire authority.
- Configuration replacement is a complete, validated, all-or-nothing snapshot.
- Arbitration uses the Home service monotonic receive clock; device wall clocks
  are metadata only.
- Device priority is a positive integer unique within a Room, with `1` highest.
- A failed winner does not promote a loser; a new wake is required.
- Credentials are distinct by owner and never enter snapshots, URLs, ordinary
  logs, diagnostics, prompts, transcripts, or audio.
- Domain policy must be testable without an HTTP framework, live Hermes endpoint,
  or physical hardware.

## Non-goals

- Hosting Hermes intelligence, answer content, speech generation, or sessions.
- Implementing iOS, Android, TUI, web, display, or firmware presentation.
- Persisting prompts, responses, transcripts, or audio.
- Device credential provisioning and lifecycle management.
- Choosing the final HTTP framework or creating a cloud/multi-household backend.

## Success signal

The first service slice can be exercised entirely with deterministic tests: an
administrator publishes a valid snapshot, a stale publisher is rejected, a
device submits eligible competing claims, exactly one claim is granted, and a
reopened service returns the committed revision without leaking credential or
conversation content.

## Assumptions

- SQLite is sufficient for the first local-household deployment and can be
  replaced behind the storage port if evidence demands it.
- The current v1 contract is stable enough to implement its foundation before
  an HTTP framework is selected.
- The first pilot remains a single household and a single Room, while the data
  model keeps multi-Room semantics.

## Open Questions

- Whether to retain the initial standard-library HTTP adapter or adopt a
  production framework and process manager.
- How are device credentials issued, rotated, expired, and revoked?
- What exact acoustic-evidence encoding, calibration, normalization, and tie
  band should the hardware contract standardize?
