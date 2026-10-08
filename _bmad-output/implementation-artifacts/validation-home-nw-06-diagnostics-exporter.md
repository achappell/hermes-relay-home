# Validation — HOME-NW-06-diagnostics-exporter

Date: 2026-10-08. Status: review. Local implementation verified; deployed acceptance, the Loki shipper and real-device captures remain pending and are not claimed.

Spec: [spec-home-nw-06-diagnostics-exporter.md](spec-home-nw-06-diagnostics-exporter.md). Owner decisions: D1-D5 approved as proposed, D6 approved in direction (local JSONL source of truth, Alloy shipper to Loki), 2026-10-08.

Installer hazard follow-up (2026-10-08): re-running `deploy/windows/install.ps1` used to reset the machine `HERMES_HOME_BIND_HOST` to `-BindHost` (default loopback) and point the local Prometheus job at that value, undoing the tailnet metrics scrape setup recorded for the CaticornQueen deployment. Fixed in source; see [validation-installer-bind-durability.md](validation-installer-bind-durability.md). Installer reruns now preserve the bind, and `-BindHost 100.78.105.19` is rejected.

## Local gates (observed)

- Test-first: the new tests were written and run red (`ModuleNotFoundError` for `hermes_home.observability.export`, and failing runtime, intake and artifact tests) before any implementation.
- Baseline before the change: 1042 passed.
- After: `uv run --python 3.14 --extra dev pytest -q` — **1088 passed**, 7 existing deprecation warnings. New and extended tests cover the store, collector, scheduler, report exporter, status, eviction split, SQLite migration, intake metrics, runtime wiring and the Grafana/Alloy artifacts.
- `tests/test_diagnostics_export.py`, `tests/test_client_reports.py` and `tests/test_runtime.py` were run five times in a row without a failure (257 passed each) to check the timing-based tests.
- `uvx ruff check src tests` — all checks passed. `uvx ruff format --check src tests` — 77 files already formatted. `git diff --check` — clean.

## Runtime smoke (local, temporary directories, no host or network beyond loopback)

A script outside the repository started the real runtime and drove it over HTTP.

In-process `create_runtime` with `HERMES_HOME_EXPORT_DIR` set and a 1 s interval:

- Status at start: `collector_state` `idle` (configured, nothing queued). After a client report and three safe events: `reachable`, `queued_event_count` 0, `last_successful_upload_at` set, drops 0 and `dropped_unuploaded_event_count` 0.
- Client report posted twice (same `report_id`): both returned 200; the export holds exactly one `client_report` line with the device ID and the report ID.
- Safe events: three `safe_event` lines followed by one `batch_ledger` line whose event IDs equal the three events and whose key is the upload's `upload-<hash>`.
- Idempotent replay: set `uploaded=0` in SQLite to mimic a lost mark after a durable write. The next tick re-sent the same batch; the export stayed at 4 lines, the queue returned to 0 and `uploads_total{outcome="success"}` rose to 2.
- After closing and creating a second runtime on the same directory, calling the new collector with the same key and events returned an exact acknowledgement and left the line count unchanged (4 to 4), so the ledger keys survive restart.
- Metrics scrape showed `hermes_home_diagnostics_collector_configured 1`, `collector_reachable 1`, `queue_depth 0`, `export_last_attempt_timestamp_seconds`, `hermes_home_export_records_written_total{record_type="client_report"} 1` and `{record_type="safe_event"} 3`, and `hermes_home_client_reports_total{outcome="accepted",platform="ios"} 1` and `{outcome="duplicate",platform="ios"} 1`, plus `hermes_home_client_reports_retained` per platform. No device, report, launch or correlation ID appeared as a label.
- `runtime.close()` returned in 0.002 s.

Real process `python -m hermes_home.runtime` with the same settings on loopback ports 18780 and 18766:

- A client report returned 200 and one `client_report` line was written; status read `idle`.
- SIGINT ended the process in 0.069 s (exit status -2, the existing uncaught `KeyboardInterrupt` behavior of `run()`, unchanged by this story).

Unit tests additionally cover: oversize batch rejection with no write; failed write leaving the idempotency key unremembered; fsync before return; owner-only file mode on POSIX; size, day and file-count rotation; per-stream retention sweeps (14 days safe events, 7 days reports) that never delete a file that may hold unexpired lines; exponential backoff 30, 60, 120, 240, 480, 900 seconds with the cap and reset; a stuck collector not holding `close()` past its bound; a full or failing report queue never raising or delaying intake, and a blocked export leaving five consecutive intakes at 200 in under a second.

## Not verified here

- Any deployment, the Windows ACL on the export directory, and `fsync` behavior on the Windows host. No host access was used.
- The Alloy snippet `deploy/windows/hermes-home-export.alloy`: no Alloy binary was available, so it is unvalidated and **not applied**.
- The Loki push URL, authentication, tenant and retention (owned by ops and unknown), and whether the ops Alloy `hermes-home` scrape is applied or the Home dashboards are provisioned.
- AC-3 and AC-4 on the deployed runtime, AC-6 scan of deployed data, AC-7 sweep on real aged files, AC-8 real-device captures (attributed Apple and Android uploads, one real dropped-connection capture), and AC-10 Grafana review.
- Both HOME-NW-06 children (`home-nw-06-client-reports`, `home-nw-06-connection-diagnostics`) remain `review`.
