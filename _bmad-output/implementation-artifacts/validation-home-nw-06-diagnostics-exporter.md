# Validation — HOME-NW-06-diagnostics-exporter

Date: 2026-10-08. Status: review. Local implementation verified; deployed acceptance, the Loki shipper and real-device captures remain pending and are not claimed.

Spec: [spec-home-nw-06-diagnostics-exporter.md](spec-home-nw-06-diagnostics-exporter.md). Owner decisions: D1-D5 approved as proposed, D6 approved in direction (local JSONL source of truth, Alloy shipper to Loki), 2026-10-08.

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

## CaticornQueen deployment — 2026-10-08 (d3d816a, PR #91)

Owner approved the deployment on 2026-10-08, after PR #91 merged. Deployed merged `origin/main` revision `d3d816a0ee40c5d44778864daacf6f510291d006` from detached worktree `.worktrees/deploy-pr91` using the trusted `caticornqueen` SSH alias only. Package-only cutover following the d803994 precedent (`validation-home-nw-06.md`, "CaticornQueen deployment — 2026-10-07"): no installer, runner or task re-registration, dependency, Prometheus, Alloy, Grafana, Standard, tailnet or credential change. No prompt, microphone, claim or phone action was performed. This records deployment and local-store evidence only; it does not claim AC-8 real-device acceptance, Loki delivery, or AC-10 Grafana review.

### Build and gates

- At the merge commit: `uv run --python 3.14 --extra dev pytest -q` — 1,088 passed, 7 warnings; `uvx ruff check src tests` and `uvx ruff format --check src tests` clean (77 files).
- Wheel SHA-256 `b8d1bb83aae3237145337d34c62368e11b685ae3f9f0f8a99546733ca77daa5d` (40 members, 35 Python sources). The host verified this hash before installing.
- Diff from the previously deployed `d803994` to `d3d816a` in `src/` is exactly seven files: new `observability/export.py`, and changes to `api/application.py`, `observability/client_reports.py`, `observability/diagnostics.py`, `observability/metrics.py`, `runtime.py`, `storage/diagnostics.py` (861 insertions, 20 deletions). No bridge, Standard, auth, credential or pairing code changed. Outside `src/`, the range adds the exporter spec and validation, the PR #88 and NW-10 documentation, Grafana dashboards, and `deploy/windows/hermes-home-export.alloy` (not deployed, not applied).

### Before

| Item | Observed |
| --- | --- |
| Package | all 34 installed source hashes equal `d803994` |
| Task | Running as SYSTEM since 2026-10-07 13:34:57 host time, pid `37536`, listeners `127.0.0.1:8780` and `:8766` |
| Machine settings | 17 `HERMES_HOME*` variables plus `HERMES_HOME_DEPLOYMENT_REVISION` = `d803994…`; no `HERMES_HOME_EXPORT_DIR` |
| Runner / task XML SHA-256 | `52b9316a…cc55` / `f61e73a2…d495` |
| Health | `/pair` 200, authenticated diagnostics status and configuration 200; configuration SHA-256 `d2e71cb9…1af3`; Prometheus `up{job="hermes-home"}` 1 |
| Diagnostics status | `collector_reachable` false, `last_successful_upload_at` null, `queued_event_count` 4096, `dropped_event_count` 2178, rejected 0 |
| Export directory | did not exist |

### Backup and rollback assets

Protected directory `C:\ProgramData\HermesHome\backups\home-pr91-20261008` (SYSTEM and Administrators only):

- `installed-package.zip` (43 files) SHA-256 `371fc296ee2f1087467f7e96ea2c0f04faff6a0ed2e99f8252385528d5341794`, `installed-package-manifest.json`.
- `home-preupgrade.sqlite3`: online `sqlite3` backup, `PRAGMA integrity_check` `ok`, 4,476,928 bytes, SHA-256 `33a8c4e6777d24bb14c01e23f4dd0e2214e63a987e13d3417e4e07f1817cbe50`.
- `run.ps1`, `task.xml`, `machine-settings.json`, `deployment-revision-before.json`.
- `rollback-package.ps1` SHA-256 `FEFAF353D36683DEC0DA2F07EC8FF5DBDE44C57947A3C341B0F14590CEC48059`, parsed with 0 errors, **not executed**. It stops only the Hermes Home task, waits for listener release, takes a fresh online database backup (`home-before-rollback.sqlite3`), restores the `d803994` package from the zip with per-file hash verification, clears machine `HERMES_HOME_EXPORT_DIR`, restores the previous `HERMES_HOME_DEPLOYMENT_REVISION`, and starts the task. It leaves the database, runner, task definition and the export directory and files in place.

Manual rollback without the script: `Stop-ScheduledTask 'Hermes Home'`; restore the package from the zip; `[Environment]::SetEnvironmentVariable('HERMES_HOME_EXPORT_DIR',$null,'Machine')`; restore the deployment revision value from `deployment-revision-before.json`; `Start-ScheduledTask 'Hermes Home'`. Disabling only the export (keeping the new code) is: clear `HERMES_HOME_EXPORT_DIR` and restart the task; status then reports `not_configured`. The database needs no rollback: the only schema change is the guarded `dropped_unuploaded_count` column, which the old code ignores.

### Cutover

1. Created `C:\ProgramData\HermesHome\diagnostics\export` with a protected DACL set on that directory only (`icacls /inheritance:r`, SYSTEM `(OI)(CI)(M)`, BUILTIN\Administrators `(OI)(CI)(R)`). The parent `diagnostics\` ACL was verified unchanged (SYSTEM Modify, Administrators Read); `HermesHome\` and `secrets\` were not touched.
2. `Stop-ScheduledTask 'Hermes Home'`; waited for the listeners to release; `uv pip install --python C:\ProgramData\HermesHome\venv\Scripts\python.exe --no-deps --force-reinstall <wheel>` (exit 0; uv's stderr progress text surfaced as a PowerShell NativeCommandError banner, as in earlier deployments, with exit code 0).
3. Set machine `HERMES_HOME_EXPORT_DIR` = `C:\ProgramData\HermesHome\diagnostics\export`; left `HERMES_HOME_EXPORT_INTERVAL_SECONDS` unset (default 30). Set machine `HERMES_HOME_DEPLOYMENT_REVISION` = `d3d816a0ee40c5d44778864daacf6f510291d006` (metadata; old value saved in `deployment-revision-before.json`).
4. `Start-ScheduledTask 'Hermes Home'`. Only that task was stopped and started.

### After (verified)

| Check | Result |
| --- | --- |
| Installed sources | All 35 installed source hashes equal the wheel and `git show d3d816a:src/<path>` (34 existing plus `observability/export.py`) |
| Task / listeners | Running as SYSTEM, last start 2026-10-08 10:12:54 host time, result `267009`, both listeners on new pid `40072` |
| Preserved | Runner and task XML hashes unchanged; parent diagnostics ACL unchanged; configuration SHA-256 unchanged (`d2e71cb9…1af3`) |
| HTTP | `/pair` 200; authenticated diagnostics status and configuration 200 |
| Tailnet front door | `/pair` 200; `/api/v1/client-claims` 401 (route reaches Home, auth enforced); Standard gateway TCP connect succeeded |
| Prometheus (local) | `up{job="hermes-home"}` 1 immediately after restart; new series present: `hermes_home_diagnostics_collector_configured` 1, `hermes_home_diagnostics_queue_depth`, `hermes_home_diagnostics_uploads_total{outcome="success"}` 16, `hermes_home_client_reports_retained{platform}`, `hermes_home_export_records_written_total{record_type="safe_event"}` 4096 |
| Status | `collector_configured` true, `collector_state` `reachable`, `queued_event_count` 0, `last_successful_upload_at` set, `dropped_unuploaded_event_count` 0 |
| Backlog | The 4,096-event backlog drained in 16 bounded batches within seconds of start. Nothing was lost from the backlog (`dropped_unuploaded_event_count` 0) |
| Export file | `safe-events.20261008.0001.jsonl` (1.9 MB). Read-only scan of all 4,115 lines: 0 invalid JSON; types `safe_event` 4,098 and `batch_ledger` 17; 0 duplicate event IDs; 0 hits for authorization, token, secret, password, prompt or transcript markers; keys limited to the safe-event fields plus ledger fields |
| Operational JSONL | New `process_started` and `provenance_header` records; unchanged behavior |

### Surprises and notes

- `dropped_event_count` kept rising (2,178 before, 2,182 shortly after) even with the queue at 0. The bounded store is full of already exported rows, so each new event evicts the oldest exported row. These evictions are counted as `after_upload` (`hermes_home_diagnostics_events_evicted_total{state="after_upload"}`) and lose nothing unexported. Earlier evictions before this deployment are not split retroactively, so they read as after-upload.
- `hermes_home_client_reports_retained{platform="ios"}` was 58 (the 2026-10-08 read-only review saw 53), so Apple clients are still reporting. No report arrived after the restart in the first minutes, so **no `client-reports.*.jsonl` file exists yet and the client-report export path is not yet observed on the host**. It was exercised only by the local smoke and tests. It will appear on the next accepted report.
- The 7-day and 14-day sweeps have not run on aged files (the export is new), and `fsync` durability on NTFS is not independently measured; both remain unverified here.
- The installed `run.ps1` still differs from the repository runner in the `logs` to `diagnostics` path, as recorded on 2026-10-04.

No rollback was needed.

### Still not claimed

AC-8 real-device captures (attributed Apple and Android automatic uploads, one real dropped-connection capture), AC-10 Grafana review, any Loki delivery (the Alloy shipper, Loki endpoint, authentication and tenant remain with ops and nothing was applied), the first exported client-report line on the host, and the sweep on aged files. Both HOME-NW-06 children remain `review`; the exporter story remains `review`.
