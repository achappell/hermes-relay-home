---
id: SPEC-home-service-foundation
updated: 2026-09-13
companions:
  - ../../planning-artifacts/architecture/architecture-hermes-relay-home-2026-09-12/ARCHITECTURE-SPINE.md
  - ../../../docs/contracts/v1/README.md
  - ../../../docs/contracts/v1/configuration.schema.json
  - ../../../docs/contracts/v1/wake-claim.schema.json
  - credential-lifecycle.md
  - '~/Documents/Vaults/Personal Vault/projects/hermes-home/sources/prds/prd-hermes-home-next-wave-2026-09-13/prd.md'
  - '~/Documents/Vaults/Personal Vault/projects/hermes-home/slices/next-feature-slate-2026-09-13.md'
  - '~/Development/hermes-relay-tui/_bmad-output/planning-artifacts/architecture/architecture-hermes-relay-tui-2026-09-08/ARCHITECTURE-SPINE.md'
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

- **CAP-6**
  - **intent:** A trusted household administrator can enroll a new endpoint from
    a five-minute, single-use code and explicit approval.
  - **success:** Scanning creates a pending request but grants no access; one
    explicit approval issues one endpoint credential, and a second use,
    expiry, cancellation, or rejection cannot issue another credential.

- **CAP-7**
  - **intent:** Home can validate, expire, rotate, revoke, and re-enroll
    opaque credentials scoped to one endpoint.
  - **success:** An active credential is accepted, expired or revoked
    credentials are rejected, re-enrollment produces a different credential
    and invalidates the old one, issued credentials expire after 90 days,
    renewal becomes available in the final 14 days with at most a 10-minute
    old-credential retry overlap, the same rotation request can be retried
    without minting another credential, and no Hermes bearer credential
    appears in any endpoint response or recorded operational data; Home's
    persistent store contains no bearer plaintext.

- **CAP-8**
  - **intent:** Revoking an endpoint stops its new household activity and makes
    late actions harmless.
  - **success:** New capture, follow-up, Watch, and notification requests are
    rejected after revocation; reachable active work is interrupted, late
    device messages do not mutate state, reconnect requires fresh
    authorization, and an endpoint that misses credential expiry must
    re-enroll.

- **CAP-9**
  - **intent:** A trusted control-plane surface can review a pending endpoint
    request and approve a bounded scope or reject it before any credential is
    issued.
  - **success:** The first pilot TUI shows the endpoint identity, confirmation
    code, expiry, requested Room, capabilities, and Profile mappings; the
    approver can select a bounded scope or reject the request, and only an
    approved request can issue a credential. Phones later use the same Home
    contract, while a display cannot approve itself.

## Constraints

- The existing v1 HTTP contract and JSON Schemas are the wire authority.
- Configuration replacement is a complete, validated, all-or-nothing snapshot.
- Arbitration uses the Home service monotonic receive clock; device wall clocks
  are metadata only.
- Device priority is a positive integer unique within a Room, with `1` highest.
- A failed winner does not promote a loser; a new wake is required.
- Credentials are distinct by owner and never enter snapshots, URLs, ordinary
  logs, diagnostics, prompts, transcripts, or audio.
- Enrollment is a two-step trust boundary: the QR or enrollment payload carries
  only a five-minute, single-use code, and scanning alone never grants access.
- Device credentials are externally opaque and scoped to one endpoint. They are
  not personal Hermes bearer tokens and do not contain an arbitrary Profile ID.
- An issued credential expires after 90 days. Home offers automatic renewal in
  the final 14 days; an old credential may overlap for at most 10 minutes only
  to recover from a lost rotation response. Explicit revocation invalidates it
  immediately, and expiry requires re-enrollment.
- Each renewal carries a client-generated request ID bound to the current
  credential generation. Retrying that same request returns the same
  replacement result; a different request cannot mint a second replacement.
- Home persists only a keyed digest of a credential. The root secret stays
  outside SQLite, retryable replacement material is encrypted and short-lived,
  and an endpoint must use platform secure storage or pairing fails closed.
- Enrollment approval is deny-by-default and scope-bound: only a trusted Home
  admin surface may approve; the endpoint cannot self-approve or grant itself
  capabilities, Rooms, or Profile mappings.
- Domain policy must be testable without an HTTP framework, live Hermes endpoint,
  or physical hardware.

## Non-goals

- Hosting Hermes intelligence, answer content, speech generation, or sessions.
- Implementing iOS, Android, TUI, web, display, or firmware presentation.
- Persisting prompts, responses, transcripts, or audio.
- Profile mapping and continuous conversation claims beyond the credential's
  endpoint identity.
- Route roaming, the transparent Hermes bridge, and endpoint QR presentation.
- Choosing the final HTTP framework or creating a cloud/multi-household backend.

## Success signal

The service can be exercised entirely with deterministic tests: an
administrator publishes a valid snapshot, a stale publisher is rejected, a
device submits eligible competing claims, exactly one claim is granted, a
trusted TUI reviews and approves bounded scope for a new endpoint, that
endpoint is enrolled and then revoked, and a reopened service returns the
committed revision without leaking credentials or conversation content.

## Assumptions

- SQLite is sufficient for the first local-household deployment and can be
  replaced behind the storage port if evidence demands it.
- The current v1 contract is stable enough to implement its foundation before
  an HTTP framework is selected.
- The first pilot remains a single household and a single Room, while the data
  model keeps multi-Room semantics.
- An already trusted phone or TUI is available to approve enrollment; the first
  slice does not need to solve cross-route discovery.

## Open Questions

- Whether to retain the initial standard-library HTTP adapter or adopt a
  production framework and process manager.
- Which keyed-digest and authenticated-encryption primitives, root-secret
  store adapter, and revocation-retention policy should the Home implementation
  use?
- What exact confirmation-code presentation, TUI command or modal wording, and
  accessibility behavior should the first approval surface use? What phone
  adapter details should follow?
- What transport and same-household identity proof must pair with roaming
  routes? This belongs to the later bridge/route slice, not the credential
  object itself.
- What exact acoustic-evidence encoding, calibration, normalization, and tie
  band should the hardware contract standardize?
