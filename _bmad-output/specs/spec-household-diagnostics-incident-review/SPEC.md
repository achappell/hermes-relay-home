---
id: SPEC-household-diagnostics-incident-review
updated: 2026-09-13
companions:
  - diagnostics-contract.md
  - ../../planning-artifacts/architecture/architecture-hermes-relay-home-2026-09-12/ARCHITECTURE-SPINE.md
  - '~/Documents/Vaults/Personal Vault/projects/hermes-home/sources/prds/prd-hermes-home-next-wave-2026-09-13/prd.md'
  - '~/Documents/Vaults/Personal Vault/projects/hermes-home/slices/next-feature-slate-2026-09-13.md'
  - '~/Development/hermes-relay-tui/_bmad-output/planning-artifacts/architecture/architecture-hermes-relay-tui-2026-09-08/ARCHITECTURE-SPINE.md'
sources:
  - ../../../docs/project-context.md
  - ../../../docs/architecture.md
---

> **Canonical contract.** This SPEC and `diagnostics-contract.md` define the
> Home-owned Household Diagnostics and Incident Review slice. Automatic safe
> telemetry, reviewable metrics/timelines, and explicit encrypted incident
> capture are separate paths with separate privacy and retention rules.

# Household Diagnostics and Incident Review

## Why

This is a debugging and privacy problem: a family product needs enough evidence
to explain a failed, slow, interrupted, or unavailable request without asking
the family to reproduce it, but automatic logging must not become covert
surveillance. The system therefore needs a safe lifecycle record for every
turn, a review path for metrics and structured timelines, and a separate
visible capture for selected content when safe evidence is not enough.

## Capabilities

- **CAP-1**
  - **intent:** The system records safe lifecycle evidence for every successful,
    failed, interrupted, and unavailable turn across the endpoint, Home, and
    Hermes bridge.
  - **success:** Each scripted outcome produces a versioned event linked by one
    opaque correlation ID and containing the permitted phase, duration, route,
    version, byte-count, health, and typed-outcome fields.

- **CAP-2**
  - **intent:** An authorized reviewer can follow one request across endpoint,
    Home, and Hermes without receiving content-bearing data in automatic
    metrics.
  - **success:** A healthy and a failed fixture produce a linked timeline and
    low-cardinality metrics; prompts, transcripts, audio, credentials, keys,
    Sensitive Entry values, and private notification content are rejected by
    the automatic schema.

- **CAP-3**
  - **intent:** An endpoint can show whether safe telemetry is operating and
    what evidence may still be queued or retained.
  - **success:** The endpoint reports enabled state, last successful upload,
    queued-event count, collector reachability, and the applicable retention
    deadline without exposing secrets or private content.

- **CAP-4**
  - **intent:** The system can hold a bounded local pre-failure record for one
    endpoint and current task or session until a person decides whether to
    capture it.
  - **success:** A 60-second ring buffer remains local and ephemeral, is
    bounded under continued activity, and is not uploaded merely because a
    failure occurred.

- **CAP-5**
  - **intent:** An authorized person can review and approve an incident bundle
    for one endpoint and its current task or session.
  - **success:** Selected detailed logs, prompts, transcripts, audio, and the
    bounded pre-failure buffer remain local until preview and explicit approval;
    the approved bundle is encrypted, separately uploaded, and receives a
    visible seven-day retention deadline.

- **CAP-6**
  - **intent:** The diagnostics system can retain, preserve, delete, and audit
    review evidence without changing the live Hermes conversation.
  - **success:** Safe metrics expire after 30 days, detailed event logs after
    14 days, and incident bundles after 7 days unless preserved; deletion
    removes data from the review path, access is auditable, and collector,
    storage, or upload failure never blocks, retries, or replays a live turn.

## Constraints

- Home owns the correlation, authorization, redaction, retention, and
  diagnostics-status policy. Clients and ops adapters do not create competing
  diagnostic authorities.
- Automatic event and metric schemas reject prompt text, transcript text,
  audio bytes, credentials, keys, Sensitive Entry values, private notification
  content, and reversible household identifiers.
- Automatic telemetry records healthy work as well as failures. Safe fields may
  include opaque endpoint/session fingerprints, app or service versions, phase
  transitions, durations, route, typed outcomes, health results, and audio or
  frame byte counts.
- Metrics remain low-cardinality and separate from detailed structured event
  timelines. Content-bearing data never enters metrics.
- Every explicit incident capture is scoped to one endpoint and its current
  task or session. It cannot silently expand to another Room, Profile,
  endpoint, or historical transcript.
- The 60-second pre-failure ring buffer remains local and ephemeral until its
  selected contents are previewed and explicitly approved.
- Incident bundles use a separate encrypted diagnostics path. They do not
  alter, mutate, or replay the Standard Hermes Channel or a live turn.
- Safe metrics retain for 30 days, detailed structured events for 14 days, and
  incident bundles for 7 days unless an authorized person explicitly preserves
  a bundle.
- Remote transport and stored diagnostic data require protection and
  auditable access. Deletion must remove records from the review path rather
  than merely hiding them from a dashboard.
- Local queues and ring buffers are bounded. Loss under outage or capacity
  pressure is visible and never delays or replays a Hermes turn.
- Endpoint status exposes telemetry enabled state, last successful upload,
  queued-event count, collector reachability, and retention deadline.
- The diagnostics path is separate from the ordinary Hermes session path and
  does not justify a new Hermes protocol channel.

## Non-goals

- Automatically exporting prompts, transcripts, audio, credentials, keys,
  Sensitive Entry values, or private notifications to remote analytics.
- Indefinite retention of detailed conversation evidence or a public,
  multi-household observability service.
- Choosing the final structured-log backend, encrypted bundle store,
  cryptographic primitives, trusted-surface role model, or audio buffer
  encoding in this kernel.
- Replacing the ordinary Hermes channel, adding remote desktop behavior, or
  making diagnostics a second session or control channel.
- Building a fleet dashboard before single-device status and current-task
  incident review work.

## Success signal

A fake endpoint, Home service, and Hermes bridge produce one linked safe
timeline for healthy, failed, interrupted, and unavailable turns. A reviewer
can inspect metrics and structured events, start a capture for one current
task/session, preview its selected evidence and 60-second buffer, approve an
encrypted bundle, observe its retention deadline, and delete or preserve it;
none of these operations blocks or replays the live turn.

## Assumptions

- The existing Home Prometheus/Grafana path remains the first review boundary;
  the concrete log and encrypted-bundle stores are selected by the owning
  implementation slice.
- Slice A supplies per-device authorization and Slice C supplies the live
  route/session identity needed to create a linked diagnostic timeline.
- An endpoint can stage a bounded local ring buffer and show a preview before
  selected detailed content leaves the household.
- A trusted control-plane surface exists to authorize capture and later
  preserve or delete a bundle; the exact surfaces and roles remain open.

## Open Questions

- Which structured-log backend and encrypted incident-bundle store extend the
  existing ops/Grafana path, and what transport protection do they require?
- Which trusted surfaces and household roles may start, preview, preserve, and
  delete incident bundles?
- Which evidence categories are selected by default, and how are detailed
  prompts, transcripts, and audio previewed without leaking them before
  approval?
- What exact bounded ring-buffer contents, audio encoding, local quota, and
  overwrite behavior are safe for each endpoint class?
- Which component enforces the safe-event schema at each boundary, and how are
  redaction failures surfaced without blocking the live turn?
- What upload retry, backpressure, and queue-loss policy makes last-upload and
  dropped-event status trustworthy during a prolonged outage?
- What audit record and remote-erasure evidence prove that an explicit deletion
  removed data from the review path?
