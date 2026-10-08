---
id: HOME-NW-06-diagnostics-exporter
parent: HOME-NW-06
status: review
product_epic: 6
created: 2026-10-08
github_issue: https://github.com/achappell/hermes-relay-home/issues/89
---

# Deployed safe-event exporter

**Status: review.** Repo-only tasks 1-8 are implemented and locally verified (see the implementation record at the end). Deployed acceptance, the Loki shipper, and real-device captures remain pending owner-approved steps; none is claimed.

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

## Decisions (D1-D5 APPROVED 2026-10-08; D6 approved direction)

### D1. Collector ownership and destination

- **APPROVED default (2026-10-08):** Home owns the destination. A Home-host-local, append-only JSONL export store in a new directory under the protected diagnostics area (separate from the operational JSONL files), written by an in-process `FileEventCollector` for safe events and a sibling sink for accepted client reports (see D6). Operator: the single household owner; the SYSTEM-owned ACL already used for `C:\ProgramData\HermesHome\diagnostics` (validation supplement, 2026-10-04) applies.
- **Rationale:** It is the cheapest option: no new service, no network, no new credential, no new dependency (`pyproject.toml:12` has no HTTP client), and it exercises the real ack, idempotency and drain path end to end. The port stays swappable.
- **Alternatives:** (a) HTTPS POST to a household receiver over the tailnet. Grafana, Prometheus, Loki and Alloy run on the household `ops` host, but no Loki push endpoint or auth is recorded anywhere readable, so the owner or ops must confirm one if wanted (see D6); (b) a separate receiver process on the Home host bound to loopback.
- **Honesty caveat:** with the default, `collector_reachable` means "the last export write was durably acknowledged by the local export store", not "a remote collector is up". Status wording and docs MUST say so.

### D2. Transport and encoding

- **APPROVED default (2026-10-08):** No network transport. Encoding is one JSON object per line (UTF-8) with a `record_type` field (`safe_event` or `client_report`). Safe events use `DiagnosticEvent.to_dict()` (`diagnostics.py:342`); one batch is appended as consecutive lines followed by a single batch-ledger line holding `idempotency_key` and the event IDs, then `flush` plus `fsync` before returning the acknowledgement. Client reports are one line each (stored payload, `device_id`, `received_at`), not part of the `EventCollector` batch/acknowledgement path. Rotation by size with a bounded file count.
- **Rationale:** Matches the existing JSONL operational sink style and makes the acknowledgement truthful (fsync before ack).
- **Alternatives:** HTTPS `POST` with the same JSON-lines body and `Idempotency-Key` header using stdlib `urllib` or `http.client` (no new dependency); gRPC/OTLP (heavier; new dependency).

### D3. Authentication and TLS

- **APPROVED default (2026-10-08):** Not applicable to the local file store. Access control is OS ACL: SYSTEM modify, Administrators read, no Users write (the posture already verified for the diagnostics directory). No credential is added to config, logs or URLs.
- **Rationale:** Avoids a new secret. If D1 is overridden to a network receiver, the default becomes: tailnet-only destination, HTTPS with certificate verification on (no skip-verify), and a static per-Home bearer secret read from a protected file named by an env var, never placed in URLs or logs. This follows the existing `HERMES_HOME_STANDARD_TOKEN_FILE` pattern (`src/hermes_home/runtime.py:196`).
- **Alternatives:** tailnet identity only (rely on tailnet ACL) with no bearer; mTLS (highest assurance, highest setup cost).

### D4. Failure, timeout and size behavior

- **APPROVED default (2026-10-08):**
  - Batch size: at most 256 events per upload and 1 MiB encoded per batch (new bounded constants; the recorder bound stays 4096).
  - Drain cadence: independent timer thread, default every 30 s, plus one final bounded drain at shutdown (timeout at most 2 s; never blocks process exit past that).
  - Per-attempt deadline: 5 s write budget. On timeout or exception the existing failure path applies (`diagnostics.py:846-851`): batch stays queued, reachability false, `uploads_total{outcome="unavailable"}` incremented.
  - Backoff: exponential 30 s to 15 min cap, with jitter; reset on success. Backoff applies only to the exporter thread, never to live work.
  - Disk-full or oversize batch: fail the batch (counted), never truncate or partially acknowledge.
- **Rationale:** Small bounds keep memory and shutdown time bounded; failure semantics are already defined, the adapter only needs to raise.
- **Alternatives:** fixed interval with no backoff (simpler, noisier); larger batches (fewer writes, longer stall on failure).
- **Loss rule (unchanged, MUST stay honest):** when the queue is full the oldest row is evicted regardless of upload state (`storage/diagnostics.py:147-174`). The exporter reduces but cannot prevent loss under sustained burst; loss stays counted and visible.

### D5. Remote retention, review and deletion responsibility

- **APPROVED default (2026-10-08):** Safe-event export lines are retained 14 days, matching `EVENT_RETENTION_SECONDS` (`diagnostics.py:24`). Client-report export lines are retained 7 days, matching the stored report retention (`client_reports.py:15`); a 14-day copy would extend it. The Home owner is the sole reviewer, through the host filesystem and the existing admin-authenticated diagnostics status, plus Grafana per D6 if approved; no new Home review route. Deletion is by the retention sweep (delete whole rotated files past each class's retention) or explicit owner deletion of the directory; a sweep result is logged as a count only. Incident bundles are out of scope (they keep their separate 7-day rule).
- **Rationale:** Same retention class and responsibility as the data already kept locally; no new data class and no new reader.
- **Alternatives:** shorter (7 days) to match incidents; longer for incident forensics (requires owner policy change); a named off-host reviewer with a documented erasure procedure (needed only if D1 changes to a remote receiver).

### D6. Grafana review path (APPROVED direction 2026-10-08; shipper detail blocked on ops)

Owner requirement 2026-10-08: the owner wants to review diagnostics logs and events, including the opted-in client reports that surfaces send automatically, in Grafana. Repo and household-note findings: Grafana, Prometheus, Loki and Alloy run on the household `ops` host (`deploy/ops/README.md:3`); Home exposes Prometheus metrics (`observability/README.md`) and has two dashboards that use no `hermes_home_diagnostics_*` series; no Home data goes to Loki today. Client reports sit only in the `client_diagnostic_reports` SQLite table (`client_reports.py:288-292`), viewable only in the signed-in `/pair` page (`application.py:1025`), and today only iOS (opt-in) sends them automatically; Android is manual Share only, and TUI, browser and ESP32 surfaces have no sender.

- **APPROVED default (2026-10-08):** (1) Prometheus counters and dashboard panels first (repo-only, no ops access). (2) For row-level review, keep the local JSONL as the source of truth and ship it with an Alloy `loki.source.file` to `loki.write` shipper on CaticornQueen. (3) Client reports are written to the same export area (AC-11) so one shipper covers both record types.
- **Rationale:** Counters are cheapest and need no new service. The shipper keeps the file as the truth and follows the household plan to redact at the collector rather than at query time.
- **Alternatives:** Home pushes directly to Loki `/loki/api/v1/push` from an in-process collector (fewer moving parts, but adds a network dependency and drops the local source of truth); a JSON-file Grafana datasource (poor fit); keep the `/pair` viewer only (does not meet the Grafana goal).
- **Blocked on ops (unknown, cannot be read without host access):** Loki push URL, auth and tenant, Loki retention, whether the ops Alloy `hermes-home` snippet (`deploy/ops/hermes-home.alloy`) is applied, and whether the Home dashboards are provisioned in the live Grafana.
- **Privacy and ACL:** the shipper needs read access to the protected export directory (today SYSTEM modify, Administrators read). Use a dedicated read-only account or document SYSTEM. Client reports are already content-free and closed-vocabulary (`client_reports.py:118-190`), stored 7 days (`client_reports.py:15`).

Changes to D1-D5 implied by D6 (all APPROVED 2026-10-08): D1 and D2 add `client_report` as a second record type with a `record_type` field; D3 adds Loki auth and tailnet-only transport only if a shipper or direct push is chosen; D4 requires that a failed export of a client report never affects intake; D5 sets client-report copies to 7 days (matching `client_reports.py:15`) and safe-event copies to 14 days.

### Cheapest path

D1-D5 defaults are the cheapest for the safe-event export: one new in-process adapter, no new service, secret, dependency or network path. D6 adds repo-only counters and panels at low cost; a log shipper or direct Loki push is the costliest step and needs ops access. The costliest alternative overall is a networked receiver with mTLS.

## Implementation tasks (after owner approval of D1-D6)

1. **Concrete adapter.** Implement the approved `EventCollector` (default: `FileEventCollector`) that returns `UploadAcknowledgement` with the exact key and event ID order, and is idempotent: a repeated `idempotency_key` MUST NOT write duplicate lines (check the batch ledger).
2. **Runtime injection.** Construct the adapter in `runtime.py` where the recorder is built (`runtime.py:358`) and pass `collector=`; settings from environment follow the existing `HERMES_HOME_*` pattern (`runtime.py:132`). If unset, collector stays `None` and status says **not configured** (see AC-2).
3. **Bounded independent flush lifecycle.** A dedicated exporter thread calls `flush(limit=...)` on the D4 cadence, off every live-turn and diagnostics-writer thread. It is started after the recorder and stopped in `Runtime.close` before `diagnostics.close` (`runtime.py:111`, mirrored in the failure path at `runtime.py:491`), with the final bounded drain.
4. **Queue and drop reporting.** Keep `queued_event_count`, `dropped_event_count` and `collector_reachable` truthful. Add an upload-attempt metric series (attempts, success, failure, last attempt time), since none exists today (PR #88: "no upload-attempt metric series"). Distinguish "evicted before upload" from "evicted after upload" in status or metrics, since the current single counter does not.
5. **Configuration and docs.** Document the export directory, ACL, retention, rotation and status semantics in the runbook and README; update validation records.
6. **Tests** (code stories only; none in this spec PR): adapter ack exactness and idempotent replay; fsync-before-ack ordering; failure and timeout leaving the queue intact; no content or unknown field emitted; bounded batch and shutdown; not-configured status; metric series; runtime wiring and close ordering.
7. **Client-report sink and intake metrics (APPROVED).** Add a bounded sink so accepted client reports (`ClientReportStore.receive`, `client_reports.py:559`) are also written to the export area, and add counters for received, rejected-by-reason, rate-limited and retained-by-platform reports. Today intake emits no metrics (`application.py:1998-2005` excludes it from request accounting).
8. **Dashboard panels (APPROVED).** Add panels for `hermes_home_diagnostics_*` (queue depth, collector state, last upload, uploads by outcome, drops) and the new client-report counters to the versioned dashboards in `observability/grafana/dashboards/`. No current panel uses any `hermes_home_diagnostics_*` series.
9. **Grafana log shipper (APPROVED as repo artifact only).** Document, and provide as repo artifacts, the Alloy/Promtail configuration or direct Loki adapter chosen in D6. Applying it on CaticornQueen or `ops` is an owner-approved ops step, not part of the code PR.

## Acceptance criteria

- **AC-1 Owner approval gate.** Satisfied 2026-10-08: D1-D5 approved as proposed, D6 approved in direction. The Loki endpoint, auth and tenant (ops-owned) must still be recorded before the shipper is applied.
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
- **AC-9 Tracker.** Only after AC-1 to AC-11 are met may the two children move from `review` to `done`, by owner acceptance, recorded in `sprint-status.yaml` and `story-index.yaml`.
- **AC-10 Grafana review (APPROVED).** The owner can see, in the household Grafana, upload and queue status for the safe-event exporter and intake counts for client reports (Prometheus path), and, if D6 approves a log shipper, at least one real client report and one real safe event as log lines. No device ID, launch ID, report ID or correlation ID is used as a metric or log label.
- **AC-11 Client reports in the export (APPROVED).** Each accepted client report is written once to the protected export area with `record_type=client_report`, its stored payload, `device_id` and `received_at`. A failed sink write never changes the client's intake response, rate limiting, or stored report. Export copies expire at 7 days.

## Dependencies and non-goals

- Depends on `HOME-NW-06` (done). Related, not blocking code: iOS `IOS-DIAG-02/03` and Android `ANDROID-DIAG-02/03` for AC-8 device captures.
- Non-goals: incident-bundle storage or encryption, new review UI or routes, content capture, changes to Standard Hermes or to any other repository, Prometheus/Grafana changes, retry or replay of live turns.

## Readiness

Ready for development of tasks 1-8. D6 decision record: option C (Alloy `loki.source.file` to `loki.write` tailing the local JSONL) is the default; option B (direct Loki push) is used only if ops makes C impractical. Blocked on ops (host access not available): Loki push URL, auth and tenant, whether the ops Alloy `hermes-home` snippet is applied, and Loki retention. Also pending: deployment and real-device captures (AC-8). No deployment or runtime acceptance is claimed.

## Implementation record (2026-10-08)

Implemented on `feat/home-nw-06-diagnostics-exporter` (stacked on the approved spec). Evidence: [validation-home-nw-06-diagnostics-exporter.md](validation-home-nw-06-diagnostics-exporter.md).

Where the code lives: `src/hermes_home/observability/export.py` (store, `FileEventCollector`, `ClientReportExporter`, `ExportScheduler`, `DiagnosticsExport`); runtime wiring in `src/hermes_home/runtime.py`; status, eviction split and metrics in `observability/diagnostics.py`, `storage/diagnostics.py`, `observability/metrics.py`; report sink, conflict type and retained counts in `observability/client_reports.py`; intake counters in `api/application.py`; dashboards in `observability/grafana/dashboards/hermes-home-diagnostics*.json`; shipper placeholder `deploy/windows/hermes-home-export.alloy`.

Deviations and additions to the approved defaults (all additive; none changes a D1-D6 decision):

1. **No preemptive 5 s write deadline.** Blocking local file I/O cannot be cancelled in Python. Isolation is by the independent drain thread, the 2 s shutdown bound (the thread is a daemon, so a stuck write cannot hold the process), and nonblocking report intake. A slow write therefore delays only the next export tick.
2. **Extra status state `idle`.** `collector_state` is `not_configured`, `reachable`, `unreachable` (events queued, last attempt failed) or `idle` (configured, nothing queued, last attempt did not succeed). Without it a freshly started, empty exporter would read as a failure.
3. **Extra setting `HERMES_HOME_EXPORT_INTERVAL_SECONDS`** (1 to 3600, default 30) so the cadence is configurable without code changes.
4. **Retention is whole-file and UTC-day based.** A file is removed only when its whole UTC day plus the stream's retention is past, so lines may live up to one day beyond their retention; no line younger than retention is ever removed.
5. **Idempotency memory is the newest 4096 batch keys** (rebuilt from the ledger lines at start). A replay of a batch older than that could be written twice; event IDs in each line let a reader deduplicate.
6. **Eviction metric counts since process start.** `hermes_home_diagnostics_events_evicted_total{state}` baselines at first observation; the persisted totals stay in status (`dropped_event_count`, `dropped_unuploaded_event_count`). The SQLite store gained a `dropped_unuploaded_count` column by guarded `ALTER TABLE`; earlier evictions are not retroactively split (they read as after-upload).
7. **Status JSON gained** `collector_configured`, `collector_state` and `dropped_unuploaded_event_count`. `collector_reachable` is forced false when no collector is configured, even if SQLite holds an older true.
8. The installer and `run.ps1` are unchanged; the operator sets `HERMES_HOME_EXPORT_DIR`.

Still pending and not claimed: AC-3 and AC-4 on a deployed runtime, AC-6 scan on deployed data, AC-7 sweep on real aged files, AC-8 real-device captures, AC-10 Grafana review (needs the Loki endpoint, authentication and tenant from ops, and the shipper applied), and any deployment. The Alloy snippet has not been validated with the Alloy binary.
