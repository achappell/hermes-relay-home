---
id: HOME-NW-06-diagnostics-exporter
parent: HOME-NW-06
status: draft
product_epic: 6
created: 2026-10-08
github_issue: https://github.com/achappell/hermes-relay-home/issues/89
---

# Deployed safe-event exporter

**Status: draft, spec only.** No source, configuration or deployment change is made or authorized by this document. Every default below is **PROPOSED, awaiting owner approval**. Implementation MUST NOT start until the owner approves or amends D1-D5.

## Owner decision (recorded)

On 2026-10-08 the owner chose **option A** from [PR #88](https://github.com/achappell/hermes-relay-home/pull/88): a deployed safe-event exporter **is a current HOME-NW-06 acceptance requirement**. `HOME-NW-06-client-reports` and `HOME-NW-06-connection-diagnostics` stay in `review` until this story's exporter is wired and the real-device gates below are met. The historical injected-port boundary (`EventCollector`, spec-home-nw-06-diagnostics-incident-review.md UPLOAD row) is kept; this story supplies the missing concrete adapter, injection and lifecycle. It does not reopen historical HOME-NW-06 acceptance.

## Problem (observed, PR #88)

Evidence: [validation-home-nw-06-connection-failure-diagnostics.md](validation-home-nw-06-connection-failure-diagnostics.md#safe-event-export--not-wired-with-observed-durable-evidence-loss), installed d803994.

- Runtime builds `DiagnosticsRecorder(store=..., metrics=...)` with no collector (`src/hermes_home/runtime.py:358-361`; fallback `src/hermes_home/api/application.py:133`). The constructor default is `collector=None` (`src/hermes_home/observability/diagnostics.py:658`).
- Nothing calls `flush()` (defined `diagnostics.py:799`). With no collector, `flush()` marks reachability false and uploads nothing (`diagnostics.py:830-832`).
- The SQLite queue is bounded at `DEFAULT_MAX_EVENTS = 4096` (`diagnostics.py:26`). `append` evicts the oldest row and increments `dropped_event_count` when full (`src/hermes_home/storage/diagnostics.py:147-174`). Observed: 4096 retained, all `uploaded=0`, drops 2060 then 2062, last upload null, no upload-attempt metric.
- `collector_reachable=false` is stored state, not a probe (`storage/diagnostics.py:306`). It is not evidence of a network or auth outage.
- Unaffected and healthy: local operational JSONL (1,551 valid records, 0 invalid) and client-report intake (53 reports). Neither is the exporter.

## Existing contract this story MUST reuse

- Port: `EventCollector.upload(events, *, idempotency_key) -> UploadAcknowledgement` (`diagnostics.py:433-441`).
- Stable idempotency key: `upload-` plus first 32 hex of SHA-256 over the NUL-joined event IDs (`diagnostics.py:2051-2054`).
- Exact acknowledgement: the returned key and the ordered event ID tuple must equal the request, else the batch is a failure (`diagnostics.py:2057-2068`; `UploadAcknowledgement` validates `evt-<32 hex>` IDs, no duplicates).
- Collector I/O happens outside the recorder lock; in-flight IDs are excluded from concurrent batches; any collector exception marks unreachable and leaves the queue intact (`diagnostics.py:799-873`).
- Success marks rows uploaded, records last-success time and reachability (`storage/diagnostics.py:262-304`).
- Events are already content-safe and schema-validated before they are queued (`DiagnosticEvent.from_mapping`). The exporter MUST NOT add fields, content or new identifiers.
- Retention constants: events 14 days, incidents 7 days, metrics 30 days (`diagnostics.py:23-25`).
- Live work never retries, replays or blocks on collector failure (parent SPEC CAP-6; diagnostics-contract.md "Automatic collector unreachable").

## Decisions for owner approval (all PROPOSED)

### D1. Collector ownership and destination

- **PROPOSED default:** Home owns the destination. A Home-host-local, append-only JSONL export store in a new directory under the protected diagnostics area (separate from the operational JSONL files), written by an in-process `FileEventCollector`. Operator: the single household owner; the SYSTEM-owned ACL already used for `C:\ProgramData\HermesHome\diagnostics` (validation supplement, 2026-10-04) applies.
- **Rationale:** It is the cheapest option: no new service, no network, no new credential, no new dependency (`pyproject.toml:12` has no HTTP client), and it exercises the real ack, idempotency and drain path end to end. The port stays swappable.
- **Alternatives:** (a) HTTPS POST to a household receiver over the tailnet (the parent SPEC mentions "the existing Prometheus/Grafana path" as the review boundary, SPEC.md:140, but no log receiver or Loki is evidenced in this repo; owner must name one if wanted); (b) a separate receiver process on the Home host bound to loopback.
- **Honesty caveat:** with the default, `collector_reachable` means "the last export write was durably acknowledged by the local export store", not "a remote collector is up". Status wording and docs MUST say so.

### D2. Transport and encoding

- **PROPOSED default:** No network transport. Encoding is one JSON object per line (UTF-8, sorted keys not required) using `DiagnosticEvent.to_dict()` (`diagnostics.py:342`), one batch appended as consecutive lines followed by a single batch-ledger line holding `idempotency_key` and the event IDs, then `flush` plus `fsync` before returning the acknowledgement. Rotation by size with a bounded file count.
- **Rationale:** Matches the existing JSONL operational sink style and makes the acknowledgement truthful (fsync before ack).
- **Alternatives:** HTTPS `POST` with the same JSON-lines body and `Idempotency-Key` header using stdlib `urllib` or `http.client` (no new dependency); gRPC/OTLP (heavier; new dependency).

### D3. Authentication and TLS

- **PROPOSED default:** Not applicable to the local file store. Access control is OS ACL: SYSTEM modify, Administrators read, no Users write (the posture already verified for the diagnostics directory). No credential is added to config, logs or URLs.
- **Rationale:** Avoids a new secret. If D1 is overridden to a network receiver, the default becomes: tailnet-only destination, HTTPS with certificate verification on (no skip-verify), and a static per-Home bearer secret read from a protected file named by an env var, never placed in URLs or logs. This follows the existing `HERMES_HOME_STANDARD_TOKEN_FILE` pattern (`src/hermes_home/runtime.py:196`).
- **Alternatives:** tailnet identity only (rely on tailnet ACL) with no bearer; mTLS (highest assurance, highest setup cost).

### D4. Failure, timeout and size behavior

- **PROPOSED default:**
  - Batch size: at most 256 events per upload and 1 MiB encoded per batch (new bounded constants; the recorder bound stays 4096).
  - Drain cadence: independent timer thread, default every 30 s, plus one final bounded drain at shutdown (timeout at most 2 s; never blocks process exit past that).
  - Per-attempt deadline: 5 s write budget. On timeout or exception the existing failure path applies (`diagnostics.py:846-851`): batch stays queued, reachability false, `uploads_total{outcome="unavailable"}` incremented.
  - Backoff: exponential 30 s to 15 min cap, with jitter; reset on success. Backoff applies only to the exporter thread, never to live work.
  - Disk-full or oversize batch: fail the batch (counted), never truncate or partially acknowledge.
- **Rationale:** Small bounds keep memory and shutdown time bounded; failure semantics are already defined, the adapter only needs to raise.
- **Alternatives:** fixed interval with no backoff (simpler, noisier); larger batches (fewer writes, longer stall on failure).
- **Loss rule (unchanged, MUST stay honest):** when the queue is full the oldest row is evicted regardless of upload state (`storage/diagnostics.py:147-174`). The exporter reduces but cannot prevent loss under sustained burst; loss stays counted and visible.

### D5. Remote retention, review and deletion responsibility

- **PROPOSED default:** Exported lines are retained 14 days, matching `EVENT_RETENTION_SECONDS` (`diagnostics.py:24`). The Home owner is the sole reviewer, through the host filesystem and the existing admin-authenticated diagnostics status; no new review route or UI. Deletion is by the retention sweep (delete whole rotated files older than 14 days) or explicit owner deletion of the directory; a sweep result is logged as a count only. Incident bundles are out of scope (they keep their separate 7-day rule).
- **Rationale:** Same retention class and responsibility as the data already kept locally; no new data class and no new reader.
- **Alternatives:** shorter (7 days) to match incidents; longer for incident forensics (requires owner policy change); a named off-host reviewer with a documented erasure procedure (needed only if D1 changes to a remote receiver).

### Cheapest path

D1-D5 defaults are the cheapest: one new in-process adapter, no new service, secret, dependency or network path. The costliest alternative is a networked receiver with mTLS.

## Implementation tasks (after owner approval of D1-D5)

1. **Concrete adapter.** Implement the approved `EventCollector` (default: `FileEventCollector`) that returns `UploadAcknowledgement` with the exact key and event ID order, and is idempotent: a repeated `idempotency_key` MUST NOT write duplicate lines (check the batch ledger).
2. **Runtime injection.** Construct the adapter in `runtime.py` where the recorder is built (`runtime.py:358`) and pass `collector=`; settings from environment follow the existing `HERMES_HOME_*` pattern (`runtime.py:132`). If unset, collector stays `None` and status says **not configured** (see AC-2).
3. **Bounded independent flush lifecycle.** A dedicated exporter thread calls `flush(limit=...)` on the D4 cadence, off every live-turn and diagnostics-writer thread. It is started after the recorder and stopped in `Runtime.close` before `diagnostics.close` (`runtime.py:111`, mirrored in the failure path at `runtime.py:491`), with the final bounded drain.
4. **Queue and drop reporting.** Keep `queued_event_count`, `dropped_event_count` and `collector_reachable` truthful. Add an upload-attempt metric series (attempts, success, failure, last attempt time), since none exists today (PR #88: "no upload-attempt metric series"). Distinguish "evicted before upload" from "evicted after upload" in status or metrics, since the current single counter does not.
5. **Configuration and docs.** Document the export directory, ACL, retention, rotation and status semantics in the runbook and README; update validation records.
6. **Tests** (code stories only; none in this spec PR): adapter ack exactness and idempotent replay; fsync-before-ack ordering; failure and timeout leaving the queue intact; no content or unknown field emitted; bounded batch and shutdown; not-configured status; metric series; runtime wiring and close ordering.

## Acceptance criteria

- **AC-1 Owner approval gate.** D1-D5 are explicitly approved or amended by the owner, and the approved values are recorded in this file replacing "PROPOSED" markers before any code merges.
- **AC-2 Honest not-configured.** With no collector configured, status and metrics report `not configured` (a distinct, documented state), never `collector_reachable=true`, and the queue does not present as a permanently growing failure. Existing missing-collector behavior (`diagnostics.py:830-832`) stays truthful for the injected-port case.
- **AC-3 Wired and draining.** In a deployed runtime with the exporter configured, the pending queue drains, `last_successful_upload_at` becomes non-null, upload-attempt metrics appear, and the 4096-event backlog observed in PR #88 is exported or its bounded loss is counted.
- **AC-4 Exactness and idempotency.** Acknowledgement key and ordered IDs match the request; a replayed key adds no duplicate export record; a mismatched acknowledgement is treated as failure.
- **AC-5 Isolation.** Exporter failure, timeout, slow disk or shutdown never blocks, retries or replays a live turn, and never exceeds the D4 shutdown bound.
- **AC-6 Privacy.** Export contents are exactly the existing safe `DiagnosticEvent` fields; no credential, prompt, transcript, audio or private identifier appears (scan evidence recorded).
- **AC-7 Retention and deletion.** The approved retention sweep demonstrably deletes expired export data, and the owner review/deletion procedure is documented.
- **AC-8 Real-device capture gates (device/owner evidence, not satisfiable by tests).**
  - A real opted-in Apple client and a real Android client each reproduce a connection failure and app restart, upload a report, and the capture is attributed (consent, failure, restart) with the matching exported Home events found through the exact scoped association.
  - One real dropped-connection capture on the pilot host shows phone schema-2 upload correlating end to end with Home and exported events.
  - Deployment evidence (installed provenance, wheel/source fingerprint, exporter status snapshot) is recorded by the deploying operator in the two NW-06 validation files.
  - Stored receive/resolve assertions and manual Android Share are not accepted as substitutes (PR #88).
- **AC-9 Tracker.** Only after AC-1 to AC-8 are met may the two children move from `review` to `done`, by owner acceptance, recorded in `sprint-status.yaml` and `story-index.yaml`.

## Dependencies and non-goals

- Depends on `HOME-NW-06` (done). Related, not blocking code: iOS `IOS-DIAG-02/03` and Android `ANDROID-DIAG-02/03` for AC-8 device captures.
- Non-goals: incident-bundle storage or encryption, new review UI or routes, content capture, changes to Standard Hermes or to any other repository, Prometheus/Grafana changes, retry or replay of live turns.

## Readiness

Draft. Blocked on owner approval of D1-D5. No implementation, deployment or runtime acceptance is claimed.
