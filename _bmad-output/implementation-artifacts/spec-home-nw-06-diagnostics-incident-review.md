---
title: 'HOME-NW-06 — Add content-safe diagnostics and incident review'
type: 'feature'
created: '2026-09-15'
status: 'done'
baseline_revision: 'fd16784d264615b6df11e02fe117871cc69a2041'
route: 'dispatch'
review_loop_iteration: 1
followup_review_recommended: false
deferred:
  - The final remote structured-log backend, encrypted bundle store, trusted-surface role model, and transport policy remain injected decisions from the canonical spec.
  - Endpoint-native capture of detailed prompts, transcripts, audio, and the physical 60-second ring buffer remains an adapter responsibility; Home owns only the scoped capture state machine and review boundary.
context:
  - '{project-root}/AGENTS.md'
  - '{project-root}/_bmad-output/specs/spec-household-diagnostics-incident-review/SPEC.md'
  - '{project-root}/_bmad-output/specs/spec-household-diagnostics-incident-review/diagnostics-contract.md'
  - '{project-root}/src/hermes_home/observability/metrics.py'
  - '{project-root}/src/hermes_home/storage/sqlite.py'

<intent-contract>

## Intent

**Problem:** Home currently exposes operational Prometheus counters but cannot
link a safe lifecycle record across the endpoint-facing bridge boundary and
Home, show honest upload/queue state, or keep explicit incident capture
separate from automatic telemetry.

**Approach:** Add a typed, content-free diagnostic event envelope and a
bounded SQLite timeline with fourteen-day event retention. Add low-cardinality
Prometheus status metrics and authenticated status/timeline reads. Model the
explicit incident lifecycle with a fixed endpoint/task scope and injected
sealer/uploader ports; automatic records never enter that bundle path.

## Boundaries & Constraints

**Always:** Keep automatic records limited to opaque fingerprints, lifecycle
phases, typed outcomes, route/health facts, durations, byte counts, queue and
upload state, and retention metadata. Use one generated correlation ID for a
logical bridge turn. Purge expired events, bound the local queue, expose loss
and upload state, and keep diagnostics failures independent from live turns.

**Never:** Store or emit prompts, transcripts, audio, credentials, keys,
Sensitive Entry values, private notification content, reversible household
identifiers, or raw network secrets in automatic diagnostics. Do not select a
final remote backend or trusted role model, create a second Hermes channel,
retry a live turn because telemetry failed, or upload incident evidence merely
because a turn failed.

**Decisions:** SQLite is the local review boundary because the service already
owns a SQLite database and the timeline needs retention across process restarts.
Remote upload is an injected collector. Incident encryption and upload are
separate injected ports so this story fixes policy and lifecycle without
inventing the final cryptographic or storage backend.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|----------------------------|----------------|
| EVENT | Typed safe event with one correlation ID | Event is retained, rendered in the matching timeline, and counted with bounded labels | Unknown or content-bearing fields are rejected and counted; live work is not blocked |
| RETENTION | Event older than fourteen days | Event is purged before reads and new writes | Purge failure leaves the live operation independent and reports storage failure to the caller |
| UPLOAD | Pending safe events and an injected collector | Collector receives safe events only; success clears queue and records last upload | Missing/failing collector leaves queue and reachability honest; no Hermes retry |
| STATUS | Admin or authenticated endpoint asks for status | Enabled state, last upload, queue depth, reachability, bounded loss, and retention policy | Missing/invalid credentials return the existing safe unauthorized envelope |
| TIMELINE | Admin asks for an opaque correlation ID | Ordered safe events for that ID, with no content-bearing fields | Invalid ID returns `400`; unknown ID returns an empty timeline |
| CAPTURE | Authorized reviewer arms one endpoint/current task | Fixed-scope capture moves through preview, approval, sealing, upload, preserve, or delete | Scope mismatch, invalid transition, expiry, or failed port is typed and cannot alter a live turn |

</intent-contract>

## Code Map

- `src/hermes_home/observability/metrics.py` — extend the existing registry
  with bounded diagnostics counters and gauges.
- `src/hermes_home/observability/diagnostics.py` — new safe event envelope,
  SQLite/in-memory stores, retention and upload status, local capture state
  machine, and injected sealer/uploader ports.
- `src/hermes_home/storage/diagnostics.py` — new transactional SQLite timeline
  and capture metadata store, isolated from configuration and credentials.
- `src/hermes_home/api/application.py` — authenticate diagnostics status and
  timeline reads without changing the existing configuration/credential
  contract.
- `src/hermes_home/api/bridge_server.py` and
  `src/hermes_home/bridge/endpoint.py` — record safe bridge lifecycle events
  with one correlation ID per submitted turn; never serialize the records to
  the endpoint.
- `src/hermes_home/runtime.py` — own one diagnostics recorder and share it with
  the HTTP and bridge adapters.
- `tests/test_diagnostics.py`, `tests/test_diagnostics_api.py`, and focused
  bridge/runtime tests — prove schema rejection, redaction, retention,
  bounded loss, upload independence, capture transitions, and correlation.
- `observability/README.md` and `_bmad-output/implementation-artifacts/validation-home-nw-06.md`
  — document the review boundary and observed evidence.

## Tasks & Acceptance

**Execution:**

- [x] Add typed safe events, bounded labels, redaction rejection, retention,
  and deterministic timeline serialization.
- [x] Add transactional local event/capture storage, bounded queue status, and
  injected collector/sealer/uploader ports with honest failure behavior.
- [x] Add authenticated status/timeline reads and safe diagnostics metrics.
- [x] Instrument the bridge adapter and runtime with one correlation ID per
  logical turn without changing endpoint payloads or retry behavior.
- [x] Add focused/full verification and record observed results in the
  validation artifact.

**Acceptance Criteria:**

- Given healthy, failed, interrupted, and unavailable bridge outcomes, when the
  adapters record lifecycle facts, then each timeline contains one opaque
  correlation ID, typed safe phases/outcomes, and no forbidden content.
- Given a forbidden automatic field or an overfull/expired queue, when Home
  records diagnostics, then the record is rejected or bounded loss is exposed,
  while the live operation continues independently.
- Given an authenticated status or timeline request, when Home responds, then
  it exposes only safe review metadata and never endpoint credentials, Profile
  IDs, runtime Session IDs, prompts, transcripts, audio, or reversible device
  identifiers.
- Given an authorized capture scope, when a reviewer previews, approves,
  preserves, or deletes it, then state transitions remain fixed to one current
  endpoint/task, sealing/upload are separate from automatic telemetry, and no
  live Hermes state is changed.

## Review Triage Log

- The automatic schema is an explicit allowlist with no content-bearing payload
  slot. Bridge diagnostics are best-effort at both event construction and store
  boundaries, so a redaction, SQLite, metrics, or collector failure cannot
  change a live response or retry a turn.
- The Home status read is side-effect free: diagnostics control routes and the
  metrics route are excluded from automatic request recording, so asking for
  queue status cannot change the queue status being reported.
- The in-memory and SQLite timelines use deterministic chronological ordering;
  SQLite persistence is shared by the runtime across restart, while incident
  evidence remains separate from automatic events.
- The canonical open questions about the final remote backends, trusted roles,
  detailed evidence selection, endpoint-native ring encoding, and transport or
  retry policy remain explicitly deferred rather than guessed here.

## Verification

See `_bmad-output/implementation-artifacts/validation-home-nw-06.md` for the
observed focused, full-suite, lint, lock, diff, and package-build results.

## Auto Run Result

Pass. The isolated NW-06 worktree was based on merged `main`; the focused and
full Home suites, Ruff checks, lock check, diff check, and sdist/wheel build
all passed. No live Hermes gateway, physical endpoint, final remote collector,
or production encrypted bundle store was claimed; those remain outside this
slice's selected boundary.

### Review Findings

BMAD code review target: Group 1 — core diagnostics and storage. Review layers
run: blind-hunter, edge-case-hunter, verification-gap, and acceptance-auditor.
The review covered the five untracked Group 1 implementation files against the
canonical diagnostics specification and the selected context documents. The
findings below are grouped by decision, patch, deferral, and rejection so that
the review record remains actionable rather than becoming a graveyard of
unowned observations.

#### Decision needed

- [x] **D1 — Require opaque automatic identifiers and typed failure codes**
  (**high**). `DiagnosticEvent` currently validates token syntax for
  `failure_code`, route ID, endpoint/session/turn fingerprints, and service
  version, but accepts caller-supplied reversible values such as
  `device-123`, `profile-123`, or `turn-on-the-lights`. Choose the contract:
  (1) generate these values inside Home and reject caller-supplied values;
  (2) accept externally supplied values only under field-specific opaque
  prefix, length, and entropy rules; or (3) retain token-shape validation and
  explicitly accept the privacy risk for this slice. Sources: blind-hunter,
  acceptance-auditor. Locations: `src/hermes_home/observability/diagnostics.py:136-148,452-486`.
  **Decision recorded (1):** Home generates opaque identifier/fingerprint
  values; callers cannot provide replacements. `failure_code` is a typed,
  allowlisted value rather than free-form caller text.

- [x] **D2 — Honor each event's retention class and deadline** (**high**).
  Recorder and stores currently purge against one fourteen-day cutoff and do
  not use the event's retention class or `retention_deadline`; an expired
  incoming event can therefore be stored until a later purge. Choose whether
  to (1) make the per-event deadline authoritative and purge before append and
  read; (2) split storage into class-specific retention policies with one
  policy owner; or (3) keep the fourteen-day event-wide boundary and amend the
  spec to remove per-event retention semantics. Sources: blind-hunter,
  edge-case-hunter, acceptance-auditor. Locations:
  `src/hermes_home/observability/diagnostics.py:163-166,482-486`,
  `src/hermes_home/storage/diagnostics.py:223-231`.
  **Decision recorded (1):** `retention_deadline` is authoritative per event;
  expired events are purged before append and read operations.

- [x] **D3 — Expose bounded ring-buffer loss** (**medium**). Capacity eviction
  and timestamp expiry remove entries silently, and the ring's head-only purge
  assumes chronological appends. Choose whether to (1) expose eviction,
  expiry, and out-of-order-drop counts in `DiagnosticsStatus` plus a metric;
  (2) expose the counts only in the diagnostics status response; or (3) make
  the endpoint adapter the sole owner of loss accounting and keep Home's ring
  deliberately silent. Sources: blind-hunter, acceptance-auditor.
  Location: `src/hermes_home/observability/diagnostics.py:666-695`.
  **Decision recorded (1):** report capacity evictions, timestamp expiry, and
  out-of-order drops through `DiagnosticsStatus` and Prometheus metrics.

- [x] **D4 — Bind incident capture to an authorized reviewer and current task**
  (**high**). The capture service trusts caller-provided endpoint and task
  scope and has no authorizer or current-session resolver. Choose whether to
  (1) inject an authorizer and current-task resolver and reject mismatches;
  (2) require a signed, route-issued scope capability that Home verifies; or
  (3) leave authorization to the future trusted-surface adapter and keep this
  service constructible only behind an internal seam. Sources: blind-hunter,
  acceptance-auditor. Locations:
  `src/hermes_home/observability/diagnostics.py:605-614,817-835`.
  **Decision recorded (1):** capture receives an injected authorizer and
  current-task resolver; endpoint or task scope mismatches are rejected.

- [x] **D5 — Make preview and approval use explicit evidence selection**
  (**medium**). `CapturePreview` exposes counts, bytes, and timestamps only;
  approval seals every buffered item rather than the evidence the reviewer
  selected. Choose whether to (1) return stable evidence descriptors and accept
  an explicit selected-ID set; (2) offer fixed safe categories (for example,
  metadata and ring summary) selected as a whole; or (3) keep all buffered
  evidence implicit and amend the acceptance criterion. Sources: blind-hunter,
  acceptance-auditor. Locations:
  `src/hermes_home/observability/diagnostics.py:697-722,838-920`.
  **Decision recorded (1):** previews expose stable evidence descriptors, and
  approval seals only the explicitly selected evidence IDs.

- [x] **D6 — Define remote preserve/delete behavior for incident bundles**
  (**high**). The uploader port has `upload()` only; `preserve()` and
  `delete()` change local state and do not perform or record remote retention
  or erasure. Choose whether to (1) extend the port with authenticated,
  idempotent preserve/delete operations; (2) make Home own only local review
  state and expose remote lifecycle as an injected adapter contract; or (3)
  remove preserve/delete from this story and defer the full lifecycle. Sources:
  blind-hunter, acceptance-auditor. Locations:
  `src/hermes_home/observability/diagnostics.py:792-795,935-963`.
  **Decision recorded (2):** Home owns local review state; remote preservation
  and erasure are exposed only through an injected adapter contract until the
  final remote bundle store is defined.

- [x] **D7 — Persist and bound capture records and audit history** (**high**).
  Capture state, evidence, and audit entries live in unbounded process memory,
  while the SQLite diagnostics store persists only automatic events and
  recorder state. A restart loses incident history and an active process can
  accumulate it without a bound. Choose whether to (1) add bounded SQLite
  capture/audit records and encrypted-payload metadata, with explicit payload
  retention; (2) persist capture/audit metadata durably but keep sealed payloads
  behind the future bundle store; or (3) keep them process-local and amend the
  restart and bounded-history expectations. Sources: blind-hunter,
  verification-gap, edge-case-hunter, acceptance-auditor. Locations:
  `src/hermes_home/observability/diagnostics.py:817-820`,
  `src/hermes_home/storage/diagnostics.py:27-58`.
  **Decision recorded (2):** persist capture and audit metadata durably with
  bounded history; keep sealed private payloads behind the future bundle store.

- [x] **D8 — Keep external collector calls outside the recorder lock**
  (**high**). `flush()` holds the recorder lock while calling the collector,
  so a slow or stuck upload blocks recording, status, and timeline reads.
  Choose whether to (1) claim a bounded batch under the lock, perform I/O
  outside it, then finalize under the lock; or (2) retain the lock and require
  a collector timeout plus a documented maximum stall. Source: edge-case-
  hunter. Location: `src/hermes_home/observability/diagnostics.py:521-557`.
  **Decision recorded (1):** claim a bounded batch under the recorder lock,
  perform collector I/O outside it, and finalize the batch under the lock.

- [x] **D9 — Make post-upload finalization retry-safe** (**high**). If remote
  upload succeeds but `mark_uploaded()` fails, the same event remains pending
  and a retry can duplicate it. Choose whether to (1) require a stable
  idempotency key and remote acknowledgement, (2) add a durable outbox/upload
  receipt before clearing the pending event, or (3) accept at-least-once
  delivery and document duplicate bundles. Source: edge-case-hunter.
  Location: `src/hermes_home/observability/diagnostics.py:544-557`.
  **Decision recorded (1):** every upload uses a stable idempotency key and
  requires a remote acknowledgement before Home finalizes the event.

- [x] **D10 — Enforce incident expiry for untouched captures** (**medium**).
  Capture expiry is checked only when the active capture is touched; an
  abandoned encrypted payload can remain resident past its deadline. Choose
  whether to (1) inject a runtime-owned reaper/scheduler; (2) require every
  capture/status operation to run a purge and document that no background
  guarantee exists; or (3) defer expiry enforcement to the future bundle
  store. Source: edge-case-hunter. Locations:
  `src/hermes_home/observability/diagnostics.py:973-1000`.
  **Decision recorded (1):** a runtime-owned reaper/scheduler enforces capture
  expiry even when the capture is not otherwise touched.

#### Patch

- [x] **P1 — Clamp the default flush batch to the recorder bound** (**medium**).
  A recorder with `max_events < 100` rejects `flush()` with its default limit.
  Location: `src/hermes_home/observability/diagnostics.py:521-524`.
- [x] **P2 — Contain metrics failures** (**medium**). A metrics backend error
  currently escapes after the event has been stored or rejected, violating
  best-effort diagnostics. Locations:
  `src/hermes_home/observability/diagnostics.py:476-499,558-576`.
- [x] **P3 — Purge ring entries by timestamp, not only from the head**
  (**medium**). An out-of-order stale entry survives a head-only purge.
  Location: `src/hermes_home/observability/diagnostics.py:666-695`.
- [x] **P4 — Clear ephemeral evidence and encrypted payloads on terminal paths**
  (**medium**). Successful upload and failure currently retain private capture
  material in the service object. Locations:
  `src/hermes_home/observability/diagnostics.py:851-918,1011-1015`.
- [x] **P5 — Recheck capture expiry at the external-operation boundary**
  (**medium**). A capture can expire between approval and sealing/upload but
  still be uploaded. Location:
  `src/hermes_home/observability/diagnostics.py:872-912`.
- [x] **P6 — Validate non-positive in-memory event bounds** (**low**).
  `InMemoryDiagnosticsStore(max_events=0)` can popleft an empty deque.
  Location: `src/hermes_home/observability/diagnostics.py:332-343`.
- [x] **P7 — Refresh queue-depth metrics after an empty-queue purge** (**low**).
  The empty pending path can return a stale gauge. Location:
  `src/hermes_home/observability/diagnostics.py:527-540`.
- [x] **P8 — Declare `mark_collector_unreachable` in the store protocol**
  (**low**). The recorder currently relies on `getattr()` because the protocol
  omits the method it calls. Locations:
  `src/hermes_home/observability/diagnostics.py:293-318,565-576`.
- [x] **P9 — Assert rendered diagnostics metric samples** (**medium**).
  Add behavioral assertions for queue, rejection, drop, reachability, upload,
  and last-upload samples rather than only testing metric object existence.
  Locations: `src/hermes_home/observability/metrics.py:4-32`,
  `tests/test_diagnostics.py:63-241`.
- [x] **P10 — Add a continued-activity ring quota test** (**medium**).
  Verify entry and byte bounds after repeated append/eviction activity.
  Location: `tests/test_diagnostics.py:244-285`.
- [x] **P11 — Add a multi-event SQLite upload integration test** (**medium**).
  Verify persisted events, bounded flush, and upload finalization together.
  Locations: `tests/test_diagnostics.py:192-241`,
  `tests/test_diagnostics_store.py:21-40`.
- [x] **P12 — Test capture encryption/upload failure and cancellation**
  (**medium**). Cover terminal transitions and cleanup for each failure path.
  Location: `tests/test_diagnostics.py:244-360`.
- [x] **P13 — Convert timestamp overflow to typed validation** (**low**).
  Huge timestamp values can raise `OverflowError` rather than
  `DiagnosticValidationError`. Location:
  `src/hermes_home/observability/diagnostics.py:1053-1057`.

#### Deferred

- [x] **W1 — Expose `IncidentCaptureService` through runtime/API**. The
  endpoint-native evidence contract and trusted-surface role model are explicit
  open decisions in the canonical specification, and no Home capture route is
  defined in this slice. Revisit when that adapter contract is approved.
- [x] **W2 — Add a separate count for storage failures and duplicate IDs**.
  The current status vocabulary does not establish whether these are rejected
  events or a separate availability/loss category; define that contract before
  changing the counter. Sources: blind-hunter, edge-case-hunter.

### Rejected

- **R1 — Add a second durable upload-receipt store now.** The selected D9
  contract makes a stable idempotency key and exact collector acknowledgement
  the local success boundary; a separate receipt store remains outside this
  slice until the final collector contract is chosen.
- **R2 — Treat an empty optional `event_id` as a duplicate-ID defect.** Empty
  IDs are currently interpreted as omitted and replaced with a generated ID;
  no acceptance criterion requires caller-controlled deduplication for empty
  IDs.
- **R3 — Treat an empty-queue status read as proof of stale reachability.**
  `collector_reachable` is intentionally last-known state; absence of pending
  events does not by itself invalidate that persisted value.
