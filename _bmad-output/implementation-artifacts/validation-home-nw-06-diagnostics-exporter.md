# Validation — HOME-NW-06-diagnostics-exporter

Date: 2026-10-08. Status: review. Local implementation verified and deployed to CaticornQueen (d3d816a); Alloy v1.20.1 is running and ships the export to Loki (4,119 lines ingested at first start, 4,126 after the ACL check below). Real-device captures and the owner's Grafana review remain pending and are not claimed.

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
- The Alloy snippet `deploy/windows/hermes-home-export.alloy` was not validated at implementation time (no Alloy binary was available). It was later validated with Alloy v1.20.1 and is installed and running on CaticornQueen; see "Alloy shipper to Loki" below.
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

### Ops metrics scrape fix (owner option a) — 2026-10-08

Owner approved fixing the ops Alloy scrape of Home. Done through the trusted `caticornqueen` alias only; ops was not accessed over SSH, and no ops, Alloy, Prometheus or Grafana setting was changed.

Root cause (read-only research, then re-verified before acting): Home listened only on `127.0.0.1:8780`, so ops Alloy's scrape of `100.78.105.19:8780` timed out (`context deadline exceeded`, not 401), and no Windows Firewall rule allowed TCP 8780. Re-verified before the change: machine `HERMES_HOME_BIND_HOST` was `127.0.0.1`; the installed `run.ps1` does not read or set it (it only overrides `HERMES_HOME_DIAGNOSTICS_DIR`) and the task XML carries no Home variables, so the machine variable is what the runtime reads (`runtime.py` `HERMES_HOME_BIND_HOST`); the local Prometheus job target was `127.0.0.1:8780` (config last written 2026-09-28); the bridge listener uses the separate `HERMES_HOME_BRIDGE_BIND_HOST` and stays on loopback.

Changes:

- Firewall: inbound allow `Hermes Home metrics from Tailscale`, TCP 8780, RemoteAddress `100.64.0.0/10`, Private profile (read back as `remote=100.64.0.0/255.192.0.0`).
- Machine `HERMES_HOME_BIND_HOST` `127.0.0.1` to `0.0.0.0` (previous value saved in `backups\home-pr91-20261008\bind-host-before.json`; the installer was not re-run).
- Restarted only the Hermes Home task (new pid `32808`, started 10:18:58 host time).
- Rollback script `backups\home-pr91-20261008\rollback-part1.ps1` (removes the rule, restores the saved bind value, restarts the task). Not executed.

Verification:

| Check | Result |
| --- | --- |
| Listeners | `0.0.0.0:8780` and `127.0.0.1:8766` (bridge unchanged) |
| Loopback and tailnet IP on CQ | `/pair` 200 on both; `/metrics` 401 without token and 200 with the admin token on `100.78.105.19:8780` |
| Serve paths | `https://caticornqueen.taila59979.ts.net/pair` 200; `/api/v1/client-claims` 401 |
| Local Prometheus | job `hermes-home` target still `127.0.0.1:8780` (config unchanged), `up` 1 |
| Home / export | diagnostics status `collector_state` `reachable`, queue 0, dropped_unuploaded 0; export file still growing |
| ops Alloy (read-only GET from the Mac) | `prometheus.scrape.hermes_home`: component `healthy`, target `http://100.78.105.19:8780/metrics` `health` `up`, scrape 29.6 ms; the earlier timeout is gone and no 401/403 appeared, so the ops token file is not the problem |
| ops Prometheus | `up{job="hermes-home",host="caticornqueen"}` 1 (was 0 for 23 days); `hermes_home_diagnostics_collector_configured` 1 is now present on ops |
| LAN exposure | from this Mac on the same LAN (192.168.0.200), `http://192.168.0.194:8780/pair` and `/metrics` both time out (curl exit 28); the same host reached over the tailnet answers 200. The tailnet-scoped rule is the only allow for 8780 |

Notes: this paragraph used to say that re-running `install.ps1` resets machine `HERMES_HOME_BIND_HOST` to `-BindHost` (default `127.0.0.1`) and rewrites the local Prometheus job target to match. That is superseded by merged PR #94 (`6575329`). In `deploy/windows/install.ps1` on `main`, an omitted `-BindHost` preserves the stored machine value (`Resolve-BindHostPlan`; loopback applies only on first install), the local Prometheus scrape target is derived independently (`Get-LocalScrapeHost`: loopback for `127.0.0.1` or a wildcard bind), a Tailscale-address bind is rejected because Tailscale Serve proxies to `127.0.0.1:8780`, and the tailnet-scoped firewall rule for TCP 8780 is created only with the explicit `-AllowTailnetMetricsScrape` switch. A redeploy through the installer therefore keeps the `0.0.0.0` bind set here; see [validation-installer-bind-durability.md](validation-installer-bind-durability.md). The package-only cutovers used so far do not touch the variable. The Mac's access to ops Alloy and Prometheus used read-only HTTP GETs only.

### Alloy shipper to Loki — installed 2026-10-08 (PR #93 fixes the snippet)

Owner approved installing Alloy on CaticornQueen once the tailnet grant existed. The grant (`tag:home` to `tag:ops` `tcp:3100`) was confirmed from CaticornQueen before acting: `Test-NetConnection 100.106.8.34 -Port 3100` True, `http://ops.taila59979.ts.net:3100/ready` returned `ready` (three repeats), guards `9090` and `12345` False. The LAN URL was not used. Only the `caticornqueen` SSH alias was used; ops was read through HTTP GETs from the Mac only.

**Release and checksums.** Grafana Alloy `v1.20.1` (published 2026-09-28, not a prerelease). `SHA256SUMS` from the release matched on the Mac and again on the host: `alloy-installer-windows-amd64.exe` `23fcfe3755f02e8a6a83f27149851322ab2b66d584e76d239d9c031f50d3a8f0`; `alloy-windows-amd64.exe.zip` `4921238b7d45bbd8ec4429ee06af734deb9f7a2a0fd98e5652b15a1bec2ad653`. The extracted binary reported `alloy, version v1.20.1 (revision 95e12cf)` with a valid Authenticode signature.

**Defect found in the snippet.** `alloy validate` failed on the committed snippet: the bare `env()` function is deprecated in v1.20.1 (`Error: validation failed`), and `alloy fmt` wanted tab indentation. The snippet was fixed to `sys.env` and canonical formatting, and then passed (`fmt` unchanged, `validate` exit 0). The fix and install notes are in PR #93; the deployed `config.alloy` is byte-identical to that file (SHA-256 `c017c2826a7759377b8ea681003cbdc01a5497d6f981b6fb9161bd333e038828`).

**Install.** Silent official installer: `/S /CONFIG=C:\ProgramData\GrafanaLabs\Alloy\config.alloy /DISABLEREPORTING=yes /USERNAME="NT SERVICE\Alloy"`, with the validated config placed first (the sample config was never used). The service was installed as the virtual account `NT SERVICE\Alloy` (no password; not LocalSystem), start mode Automatic, in `C:\Program Files\GrafanaLabs\Alloy`. `/DISABLEREPORTING=yes` keeps Alloy's usage reporting off.

**Installer problem.** `/ENVIRONMENT=HERMES_HOME_LOKI_PUSH_URL=http://...` was stored truncated as `HERMES_HOME_LOKI_PUSH_URL=http:`, and the installer started the service immediately. It was stopped about seconds later, before the export directory was readable by the account and before any push could occur, and the registry value `HKLM\Software\GrafanaLabs\Alloy\Environment` was set directly to `HERMES_HOME_LOKI_PUSH_URL=http://ops.taila59979.ts.net:3100/loki/api/v1/push`.

**Permissions.** `NT SERVICE\Alloy` was granted `(OI)(CI)(RX)` on `C:\ProgramData\HermesHome\diagnostics\export` only, and `(OI)(CI)M` on `C:\ProgramData\GrafanaLabs\Alloy\data` only (positions file). The parent `diagnostics\` ACL was verified unchanged; `HermesHome\` and `secrets\` were not modified.

**Verification (Alloy UI `127.0.0.1:12345`, loopback only; Home metrics; LogQL from the Mac).**

| Check | Result |
| --- | --- |
| Service | Running as `NT SERVICE\Alloy`; listener `127.0.0.1:12345` only |
| Components | `loki.write.household`, `loki.process.hermes_home_export`, `local.file_match.hermes_home_export`, `loki.source.file.hermes_home_export` all `healthy` |
| Alloy metrics | `loki_source_file_read_lines_total` 4,119 = file line count 4,119; `loki_write_sent_entries_total` 4,119 (1.94 MB); every `loki_write_dropped_entries_total{reason}` 0; `loki_write_batch_retries_total` 0; push requests `204` |
| Loki (GET from the Mac, `http://100.106.8.34:3100`) | `{job="hermes-home-export",host="caticornqueen"}`: 4,119 entries over 1 h; by `record_type`: `safe_event` 4,100 and `batch_ledger` 19 (sum 4,119 = sent); stream labels `job`, `host`, `record_type` plus `filename`, `service_name`, `detected_level` that Alloy and Loki add |
| Guard ports from CaticornQueen | 3100 True; 9090 and 12345 False |
| Home | export status `reachable`, queue 0, `dropped_unuploaded_event_count` 0; Hermes Home task Running, untouched by the Alloy install |
| Client reports | **No `client-reports.*.jsonl` exists yet**: only `safe-events.20261008.0001.jsonl` is in the export directory (1,898,990 bytes, last written 10:19:29 host time). No new client report has been accepted since the 10:12 cutover, so the client-report export and its Loki path are still unobserved on the host |

The existing 1.9 MB file was ingested once at first start, stamped with ingestion time (not event time). If Alloy's data directory (positions) is wiped, the files are ingested again and lines duplicate in Loki; the household Loki accepts duplicates (ingestion-time stamps), and removing them needs an ops-side delete.

**Open item (settled 2026-10-08 10:55 host time, see the ACL section).** `positions.yml` recorded offset `432` for the 1.9 MB file right after the first ingest, and `loki_source_file_read_bytes_total` read 432 while read lines, sent entries and the Loki count equalled the full file. After new export lines arrived the offset followed the file: at size 1,901,440 bytes (4,126 lines) the recorded offset is 1,900,393, the start of line 4,124, so it trails the end by 1,047 bytes (the last 3 lines), and `loki_source_file_read_bytes_total` equals it. A restart now re-reads at most about 3 lines, not about 4,100; Loki holds 4,126 entries = lines sent = lines in the file, so no duplicates exist. Only the window between the first ingest and the first new export line carried the large duplicate-on-restart risk, and no restart happened in it. A restart itself was still not tested.

**Rollback.** Stop Alloy and uninstall: `Stop-Service Alloy; & "$env:ProgramFiles\GrafanaLabs\Alloy\uninstall.exe" /S`; remove the ACL entries (`icacls <export dir> /remove "NT SERVICE\Alloy"`, same for `C:\ProgramData\GrafanaLabs\Alloy\data`); remove `C:\ProgramData\GrafanaLabs` if no longer wanted. The Home export, Home task and local JSONL are independent and unchanged. Loki data from this job ages out after the household Loki's 30 days; sooner purge is an ops-side delete. If the grant is withdrawn, Alloy backs off and buffers, then drops; stop the service.

**Not claimed.** AC-10 (the owner has not reviewed this data in Grafana; no Loki datasource, dashboard import or review was done here), AC-8 real-device acceptance, or the first client-report export line.

### HermesHome ACL lockdown — 2026-10-08 (owner approved)

**Finding.** Listing ACLs (names and principals only; no contents read) showed that `C:\ProgramData\HermesHome` inherited `BUILTIN\Users` read and execute (`RX`, plus create-file/create-folder) from `C:\ProgramData`, so 2,927 of 3,310 objects were Users-readable, including `home.sqlite3`, `run.ps1`, `secrets\conversation-grants.json`, its two `.bak` copies, three `home-before-*.sqlite3` copies in `secrets\`, `app\`, `venv\`, `python\`, `repairs\`, `logs\` and the `backups\` copies. Already protected (SYSTEM and Administrators only) and unaffected: `admin-token`, `credential-root`, `standard-token`, `live-gate-signing.key`, `home-before-main-73d58e5.sqlite3`; also protected: `diagnostics\`, `diagnostics-legacy\`, `provenance\`, `diagnostics\export\` and the dated `backups\` snapshot folders. This predated all NW-06 work.

**Before-state check.** Service and task logon accounts were reviewed first: Home runs as SYSTEM (scheduled task), local Prometheus, `windows_exporter` and Tailscale run as LocalSystem, Alloy as `NT SERVICE\Alloy` (explicit read and execute on `diagnostics\export` only; it reaches the path through traverse-checking bypass). Prometheus reads Home over HTTP, not from disk. No non-admin principal reads `HermesHome` through the Users grant.

**Change (applied 10:45 host time).** Disabled inheritance on `C:\ProgramData\HermesHome` converting to explicit entries and removed `BUILTIN\Users`: the root now has `SYSTEM (OI)(CI) F`, `Administrators (OI)(CI) F`, `CREATOR OWNER (OI)(CI)(IO) F`. Nothing else was edited. A full ACL save (`icacls /save ... /T /C`, 5,684 entries, names and ACLs only) was taken first, at 10:42, into `backups\home-acl-lockdown-20261008\HermesHome-acl.txt` (SHA-256 `B0CC84AB…6711F`); the folder is protected (SYSTEM and Administrators full only) and holds `rollback-acl.ps1` (SHA-256 `C161F784…E32580`).

**After-state.** Scan of all 3,311 objects: 0 grant `Users`, `Authenticated Users` or `Everyone`. `home.sqlite3`, `run.ps1`, `secrets\*` and the `.bak` and `home-before-*` copies now list only SYSTEM and Administrators. The protected-object list is identical to before. Compared with the saved file, 107 objects are unchanged and 5,300 differ only by the removed Users entries. 277 objects differ in more: all are in `venv\Lib\site-packages` (276 files) plus the root. On those files the change also dropped stale inherited entries for `CaticornQueen\achap` (full control), the local group `CaticornQueen\CodexSandboxUsers` (modify) and one unresolvable domain SID (modify); inheritance propagation rebuilt them from the parent. No other ACE was added or removed anywhere. Surprise: a sandbox group had modify on files in the venv that Home executes as SYSTEM; that is gone.

**Verification after (10:50-10:55 host time).** Hermes Home task Running (SYSTEM, last start 10:18:58, not restarted); `/pair` 200 on loopback, the tailnet IP and the Serve URL; authenticated `/api/v1/diagnostics/status` 200, `collector_state` `reachable`, queue 0, `dropped_unuploaded_event_count` 0; Alloy, local Prometheus, `windows_exporter` and Tailscale services Running; all four Alloy components `healthy`, no warn or error events in the Alloy log; `loki_write_dropped_entries_total` 0. An unauthenticated 401 probe added export lines: file 1,899,997 to 1,901,440 bytes (4,122 to 4,126 lines), `loki_write_sent_entries_total` 4,122 to 4,126, Loki `count` 4,126. Local Prometheus `up{job="hermes-home"}` 1; ops Prometheus `up{job="hermes-home",host="caticornqueen"}` 1 and `hermes_home_diagnostics_collector_configured` 1 (read-only GETs from the Mac). Home and Alloy were not restarted. Kept as before: `diagnostics\` (SYSTEM modify, Administrators read), `diagnostics\export` (explicit, protected, `NT SERVICE\Alloy` `RX`), `C:\ProgramData\GrafanaLabs\Alloy\data` (`NT SERVICE\Alloy` modify; its `Everyone (RD,RA)` entry comes from the Alloy installer and is outside HermesHome). `secrets\` itself still inherits, now from the cleaned root.

**Alloy positions.** File 1,901,440 bytes, offset 1,900,393, 1,047 bytes (3 lines) behind; see the open item above.

**Rollback.** `icacls C:\ProgramData /restore C:\ProgramData\HermesHome\backups\home-acl-lockdown-20261008\HermesHome-acl.txt /C` (the saved paths are relative `HermesHome\...`, so the restore target is the parent; also stored as `rollback-acl.ps1` in that folder). Verified on a scratch tree: restoring from the parent brings Users back on the root and children. It restores the stale `achap`, `CodexSandboxUsers` and unresolvable-SID entries too. No Home or Alloy restart is needed.

Note: processes in a non-elevated `achap` desktop session no longer read `HermesHome` (Windows filters the Administrators token); elevated sessions and SSH are unaffected.

## Grafana review (AC-10) — 2026-10-08, partial

**Datasources and import.** Owner imported the two Home dashboards (`hermes-home-diagnostics` and `hermes-home-diagnostics-logs`) into the household Grafana instance (grafana.chappell-home.dev) using the existing Loki datasource (uid `P8E80F9AEF21F6940`, http://loki:3100) and Prometheus datasource (uid `PBFA97CFB590B2093`, http://prometheus:9090). Both datasources are healthy. The dashboards were imported by hand in the Grafana UI (not provisioned from the repository), and the stored dashboard JSON retains the datasource variables `${DS_PROMETHEUS}` and `${DS_LOKI}`.

**Owner observations (by eye; no screenshots captured).** The owner reports seeing numbers displayed in the panels when viewing the Grafana dashboards.

**API checks (read-only, 2026-10-08 ~14:00 host time).** A worker queried the Loki and Prometheus APIs using a Viewer-scope service-account token on behalf of this record:

| Datasource | Query | Result |
| --- | --- | --- |
| Loki | `sum by (record_type) (count_over_time({job="hermes-home-export",host="caticornqueen"}[24h]))` | safe_event 4,105; batch_ledger 21; client_report none |
| Prometheus | `up{job="hermes-home"}` | 1 |
| Prometheus | `hermes_home_diagnostics_collector_configured` | 1 |
| Prometheus | `hermes_home_diagnostics_queue_depth` | 0 |
| Prometheus | `hermes_home_diagnostics_uploads_total` | series present |

**AC-10 acceptance status: not met.** AC-10 requires the owner to see a real client report and a real safe event in Grafana. Safe events are visible (4,105 lines in Loki over 24h). **No client_report record has been exported**: no iOS or macOS client has reported to Home since the 2026-10-08 10:12 CDT cutover; the client-report panels in Grafana are empty. AC-8 real-device captures are also still open. Both HOME-NW-06 children (`home-nw-06-client-reports`, `home-nw-06-connection-diagnostics`) and the exporter story remain `review`.

**Operational note:** A service-account token exposure was identified and the token was rotated by the owner. No token value or prefix is recorded here.

### Still not claimed

AC-8 real-device captures (attributed Apple and Android automatic uploads, one real dropped-connection capture), AC-10 Grafana review (a Loki datasource, dashboard import and the owner's own review are not done), the first exported client-report line on the host, an Alloy service restart (positions are now known to follow the file; the restart itself was not exercised), and the sweep on aged files. Both HOME-NW-06 children remain `review`; the exporter story remains `review`.
