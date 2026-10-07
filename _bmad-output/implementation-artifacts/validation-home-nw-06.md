---
story: HOME-NW-06
status: passed
validated: 2026-09-16
baseline_commit: fd16784d264615b6df11e02fe117871cc69a2041
---

# HOME-NW-06 validation

Validated in the isolated `feat/home-nw-06-diagnostics` worktree on Python
3.14. The worktree started from the merged HOME-NW-03 deployment-wiring
mainline.

## Checks

| Check | Command | Result |
| --- | --- | --- |
| Focused diagnostics/integration suite | `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q tests/test_diagnostics.py tests/test_diagnostics_store.py tests/test_diagnostics_api.py tests/test_bridge_endpoint.py tests/test_bridge_server.py tests/test_runtime.py tests/test_observability_artifacts.py` | `94 passed in 1.11s` |
| Full Home suite | `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q` | `264 passed in 3.04s` |
| Ruff lint | `uv run --no-cache --no-project --python 3.14 --with ruff -- ruff check src tests` | `All checks passed!` |
| Ruff format | `uv run --no-cache --no-project --python 3.14 --with ruff -- ruff format --check src tests` | `44 files already formatted` |
| Lockfile | `uv lock --check` | `Resolved 11 packages` |
| Whitespace/diff | `git diff --check` | Passed |
| Package build | `uv run --no-cache --no-project --python 3.14 --with build -- python -m build --sdist --wheel` | Successfully built the sdist and wheel |

## Boundary evidence

- `DiagnosticEvent` accepts an explicit safe-field allowlist only. Prompts,
  transcripts, raw audio, credentials, keys, Sensitive Entry values, private
  notification content, and reversible identifiers are rejected or omitted;
  endpoint, session, turn, and custom route values used in events are opaque
  fingerprints or Home-approved safe identities, and failure codes are typed.
- `SQLiteDiagnosticsStore` durably retains the safe event timeline across a
  Home restart, honors per-event retention deadlines, purges before reads and
  writes, bounds the local queue, and keeps upload/rejection/loss state visible.
  Prometheus metrics remain low-cardinality and carry no content; ring eviction,
  expiry, and stale out-of-order drops are visible in status and metrics.
- Home exposes authenticated status and admin-only correlation timelines. The
  diagnostics status and metrics reads do not record themselves, so a review
  read cannot mutate the state it reports.
- The bridge records one correlation across endpoint, Home, and Hermes turn
  facts. Audio records include lifecycle and PCM byte-count facts only; raw PCM
  remains a bridge payload and never enters the automatic event store. Timeout,
  failed, interrupted, and unavailable fixtures preserve typed outcomes without
  turn retries.
- The explicit capture service requires an injected authorizer and current-task
  resolver, previews stable selectable evidence descriptors from the bounded
  local ring before approval, seals and uploads through separate injected
  ports, exposes a seven-day deadline, and audits preserve/delete and expiry
  transitions. Capture metadata and audit history persist in bounded SQLite
  tables; untouched captures can be reaped through the runtime scheduler.
  Automatic telemetry never feeds this bundle path.
- Recorder upload claims a bounded batch under its lock, performs collector I/O
  outside the lock, and finalizes only after an exact acknowledgement carrying
  the stable idempotency key and event IDs. A failed finalization can therefore
  retry the same batch without changing the live turn path.

## Limits

The final structured-event collector, encrypted incident-bundle store,
cryptographic primitive, trusted-surface role model, endpoint-native detailed
evidence/ring adapter, production runtime capture route, and production upload
retry policy remain deferred by the canonical specification. The runtime
therefore owns a durable local queue and an injected collector boundary; it does
not claim a live remote collector, live capture deployment, or live
Hermes/physical-endpoint validation.

## Follow-up review validation

The follow-up BMAD review was run on 2026-09-16 against the narrowed core
diagnostics/storage implementation group. The focused core suite was rerun
after the fixes and the new negative, restart, bounds, lifecycle, clock, and
rendered metrics checks.

| Check | Command | Result |
| --- | --- | --- |
| Focused core suite | `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q tests/test_diagnostics.py tests/test_diagnostics_store.py tests/test_metrics.py` | `55 passed in 0.16s` |
| Ruff lint | `uv run --no-cache --no-project --python 3.14 --with ruff -- ruff check src tests` | `All checks passed!` |
| Ruff format | `uv run --no-cache --no-project --python 3.14 --with ruff -- ruff format --check src tests` | `46 files already formatted` |
| Whitespace/diff | `git diff --check` | Passed |

## Post-rebase integration verification

The post-merge review-fix commit was replayed onto current `main` at
`eb95002`, so the Home NW-04 `bridge.routes` module is available to the
diagnostics branch. The old worktree's export-only `bridge/__init__.py` edit
matched current `main` exactly; it is preserved in the local stash
`preserve NW-04 route exports while rebasing NW-06 review follow-up onto main`
and was not reapplied. Full-suite collection then exposed a stale API-test
helper that generated 64-character hashes instead of the required 32-hex
opaque correlation IDs. The fixture now follows the production and other test
helpers' format.

| Check | Command | Result |
| --- | --- | --- |
| Focused diagnostics suite | `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q tests/test_diagnostics.py tests/test_diagnostics_store.py tests/test_diagnostics_api.py tests/test_metrics.py` | `59 passed in 0.46s` |
| Full Home suite | `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q` | `337 passed in 3.53s` |
| Ruff lint | `uv run --no-cache --no-project --python 3.14 --with ruff -- ruff check src tests` | `All checks passed!` |
| Ruff format | `uv run --no-cache --no-project --python 3.14 --with ruff -- ruff format --check src tests` | `48 files already formatted` |
| Whitespace/diff | `git diff --check` | Passed |

## CaticornQueen deployment — 2026-10-04

Deployed the merged Home main revision `a45f7efe318e91bde7edf4c23c62973d7fb86e96`
to the live Windows Home host CaticornQueen using an isolated `.worktrees/`
checkout. The rollout was package-only, followed by a narrowly scoped runner
update to route operational diagnostics to a separate protected directory.
No broad installer, Prometheus, Standard credentials/settings, pilot proxy,
iOS, or private hub changes were made.

### Build and rollback evidence

- Detached worktree HEAD matched `origin/main` at merge commit
  `a45f7efe318e91bde7edf4c23c62973d7fb86e96` and includes HOME-NW-06.
- Home gates: `uv run --python 3.14 --extra dev pytest -q` — 913 passed,
  four deprecation warnings; `uvx ruff check src tests` — passed;
  `uvx ruff format --check src tests` — 74 files already formatted.
- Built wheel `hermes_relay_home-0.1.0-py3-none-any.whl`, SHA-256
  `dc28b3c1e71a763fa9eb6ae1ffb698c2a2c217e6bbf592cd25d0c5f11e0af016`.
  Its 39 members include 33 `hermes_home` Python sources and top-level
  `hermes_home_diagnostics.py`.
- Installed previous receipt: revision
  `082e5938615ae4fc037ed6b3a1e791ccda04bd89`, wheel SHA-256
  `42eecc57e9c655d1eba3ed39b3ad61c5da0264d6d60bbd051777e12eeca9349b`.
- Protected rollback directory:
  `C:\ProgramData\HermesHome\backups\home-nw06-a45f7ef-20261004`.
  Prior `installed-package.zip` SHA-256:
  `2493dabeee2464dd978d0d2abfc64190395a213dc18a8004552006525726ebd7`;
  its 43-file manifest was checked against archive contents and digests.
  The directory retains the prior runner, exact task XML, archived machine
  settings, distribution metadata, prior receipt and package rollback script.
  A separate task-specific `deployment-rollback.ps1` was written and parsed
  by Windows PowerShell 5.1. It stops only Home, requires both listeners to
  release, restores prior package files from the verified archive and checks
  hashes, restores the prior runner and task XML, and starts Home. It does not
  restore the older SQLite image or machine settings. The live DB was not
  reverted.
- Before cutover, all 18 current machine `HERMES_HOME*` values matched the
  archived machine settings; prior task action, SYSTEM principal and working
  directory were captured. The original user-writable logs directory was
  preserved as `C:\ProgramData\HermesHome\diagnostics-legacy`.
- Dedicated diagnostics directory
  `C:\ProgramData\HermesHome\diagnostics` has a protected ACL with only
  SYSTEM Modify and BUILTIN\Administrators Read. The final runner SHA-256 is
  `52b9316aac157fcc3095373cd8231083841b1c864c0eb7b6e88135484985cc55`; it
  sets `HERMES_HOME_DIAGNOSTICS_DIR` per Home process to this directory.
  Task action, principal, working directory, and task settings were retained.

### Live acceptance observed

| Check | Result |
| --- | --- |
| Installed package | Version `0.1.0`; source revision is the merged wheel from `a45f7efe318e91bde7edf4c23c62973d7fb86e96`; wheel SHA matches staged artifact. |
| Home task | `Hermes Home` Running as SYSTEM; last result `267009` (`0x41301`, running). Both loopback listeners on `127.0.0.1:8780` and `127.0.0.1:8766` were present on the same Home process. |
| Pairing page | `GET /pair` returned 200. |
| Authenticated Home reads | Existing admin credential (used only in the remote process) obtained 200 from `/api/v1/diagnostics/status` and `/api/v1/configuration`; no credential was emitted. |
| Prometheus | `up{job="hermes-home"}` returned `1`; no Prometheus configuration or service changes. |
| Operational JSONL | Benign `/pair` reads were followed by queued writer wait. `diagnostics/home.jsonl` contains two complete JSON records: `provenance_header` and `process_started` (`component=home`, `phase=startup`). Both parse; zero invalid records; scans found no authorization, credential/path secrets, prompt, or transcript markers. |
| Diagnostics ACL | Still protected; SYSTEM Modify and Administrators Read only, two explicit entries; no Users or deployment-account write access. |
| Proxy boundary | No proxy process was present. The task named `\Microsoft\Windows\Autochk\Proxy` is the unrelated disabled Windows Autochk task; it was not altered. No pilot proxy was started. |

### Acceptance boundary

Deployment and protected startup-log validation are complete. No synthetic
schema-2 report was uploaded and no phone observation was fabricated.
Real-device schema-2 upload and end-to-end phone/Home correlation evidence
remain pending; issue #16 remains open for those acceptance items. No commit
or push was made.

## CaticornQueen deployment — 2026-10-05 (0effbf9)

Deployed Home `main` revision `0effbf98fd1f4521313ba19f2378089ba7b3baf8`
(origin/main; includes #74 audio-wait-after-speech, #75 opt-in `turn.alive`
keep-alive, #76 runner comment) to CaticornQueen from the isolated detached
worktree `.worktrees/deploy-0effbf9`. Package-only cutover: no installer, no
runner/task re-registration, no dependency, Prometheus, Standard, tailnet or
credential change. Media-server, Standard, the Qwen3 server and iOS were not
touched, and no prompt or Home traffic beyond authenticated read GETs was sent.

### Build

- Gates in the worktree (HEAD verified as the exact SHA):
  `uv run --python 3.14 --extra dev pytest -q` — 925 passed, four deprecation
  warnings; `uvx ruff check src tests` — passed;
  `uvx ruff format --check src tests` — 74 files already formatted.
- Wheel `hermes_relay_home-0.1.0-py3-none-any.whl`, SHA-256
  `d4e1c38f53e8c5f15547bb4c9444fc5bd2ce5ed1e161535f922771f6400644c7`
  (39 members; 34 Python sources = 33 `hermes_home` + `hermes_home_diagnostics.py`).
  Every Python source in the wheel is byte-identical to
  `git show 0effbf9:src/<path>`. The wheel's SHA-256 matched on the host.
- Versus the previous deployment (`a45f7ef`), three installed sources differ:
  `hermes_home/api/bridge_server.py`, `hermes_home/bridge/endpoint.py`,
  `hermes_home/bridge/standard.py`. All other installed hashes are unchanged.

### Pre-flight (read-only) and backups

- Task `Hermes Home` Running as SYSTEM (last result `267009`); both loopback
  listeners (`127.0.0.1:8780`, `127.0.0.1:8766`) on one process (pid 28988).
  All 34 installed Python sources matched `git show a45f7ef:src/<path>`
  (so the installed code was the `a45f7ef` deployment). Runner SHA-256
  `52b9316aac157fcc3095373cd8231083841b1c864c0eb7b6e88135484985cc55`, equal to
  the new source `run.ps1`, so the runner was not replaced. 18 `HERMES_HOME*`
  machine variables recorded. Baseline `/pair`, authenticated
  `/api/v1/diagnostics/status`, `/api/v1/configuration` returned 200;
  Prometheus `up{job="hermes-home"}` = 1.
- Protected rollback directory (SYSTEM, Administrators, deployment account):
  `C:\ProgramData\HermesHome\backups\home-0effbf9-20261005`
  - `installed-package.zip` (44 files, venv-relative) SHA-256
    `16af3fc25e1572d74e2b086e659e1dc4e935b84fbba88d5e0ceb6b47ba4839f1`, with
    `installed-package-manifest.json` and `installed-distributions.json`.
  - `home-preupgrade.sqlite3`: consistent `sqlite3.Connection.backup` online
    copy (live DB reported `journal_mode=delete`); `PRAGMA integrity_check`
    `ok`; 3,579,904 bytes, SHA-256
    `15a2625d02f2cd3177b5249bbdea7653f6b5659606354f5e4383c7a5d16fbf7e`.
  - `run.ps1`, `task.xml` (`Export-ScheduledTask`), `machine-settings.json`
    (18 variables), `preflight-hashes.json`, and the deployed wheel.
  - `rollback-package.ps1` (SHA-256
    `fe4cdd347ef2b40c08be3d290e983e53ec5e8ddecc9f0f79aa3094735625d54e`),
    parsed by Windows PowerShell 5.1 with 0 parse errors; **not executed**.
    Like the previous rollback it stops only Home, requires both listeners to
    release, validates the archive checksum and manifest, saves a new
    `home-before-rollback-<UTC>.sqlite3`, restores the `a45f7ef` package files
    and verifies hashes, verifies the runner hash and task definition, and
    starts Home. It does not restore the SQLite image or machine settings.

### Cutover

`Stop-ScheduledTask 'Hermes Home'`; both listeners released;
`uv pip install --python C:\ProgramData\HermesHome\venv\Scripts\python.exe
--no-deps --force-reinstall <backup-dir>\hermes_relay_home-0.1.0-py3-none-any.whl`;
`Start-ScheduledTask 'Hermes Home'`. Only that task was stopped/started.

Deviation: the first install attempt aborted immediately because the script's
`$ErrorActionPreference='Stop'` turned `uv`'s stderr progress line into a
PowerShell error. Nothing had been installed (the installed source hashes were
re-read and equal to pre-cutover; no `uv` process was running; the task was
stopped, listeners released). The same command was re-run with the native-call
error preference relaxed and exited 0. The staging copy was placed in
`C:\Users\achap\hermes-home-deploy\deploy-0effbf9\` (a subdirectory, so the
existing wheel in the parent directory was not overwritten).

### Live acceptance observed

| Check | Result |
| --- | --- |
| Installed sources | All 34 installed Python sources equal the wheel's (and `git show 0effbf9:src/<path>`) hashes. Distributions unchanged: cffi 2.1.1, cryptography 50.0.1, hermes-relay-home 0.1.0, pycparser 3.0, segno 1.6.6, websockets 17.1. |
| New code present | `_PRE_SPEECH_AUDIO_POLL_SECONDS` in installed `bridge/standard.py`; `TURN_KEEPALIVE_INTERVAL_SECONDS` in installed `bridge/endpoint.py`. |
| Task / listeners | `Hermes Home` Running as SYSTEM, last start 2026-10-05 12:00:36 (host time), result `267009`. `127.0.0.1:8780` and `127.0.0.1:8766` on the same new process (pid 14528). |
| HTTP | `/pair` 200; authenticated `/api/v1/diagnostics/status` 200; authenticated `/api/v1/configuration` 200 with a response SHA-256 identical to the pre-cutover response. The existing admin credential was read only inside the remote process and never printed. |
| Prometheus | `up{job="hermes-home"}` returned `1`. |
| Preserved | 18 `HERMES_HOME*` machine variables equal the backup (0 differences); exported task XML SHA-256 identical before/after (`f61e73a2…d495`); runner SHA-256 unchanged (`52b9316a…cc55`); diagnostics ACL unchanged (SYSTEM Modify, Administrators Read). |
| Operational JSONL | The new `diagnostics/home.jsonl` holds two complete records (`provenance_header`, `process_started`); 0 invalid; zero authorization/bearer/token/secret/password/credential/prompt/transcript hits. The previous run's file rotated to `home.jsonl.1` (166 records, 0 invalid; its only marker hits are the benign `operation` label `prompt_submit`). |

### Boundaries

No rollback was needed. The keep-alive is opt-in per client request header, so
it takes effect only for clients that advertise it; the audio-wait change
applies to every Standard turn. No live prompt, Standard Session, real-device
turn, or phone observation was exercised, so the effect of #74/#75 on a real
phone turn remains unobserved. `HERMES_HOME_DEPLOYMENT_REVISION` is still the
stale `376583d…`; the installed-source hashes above identify this deployment.

## Deployment revision variable corrected — 2026-10-06

The `HERMES_HOME_DEPLOYMENT_REVISION` machine variable on CaticornQueen was
changed from the stale `376583d7273e08a090d7ec33c416e0b32880a343` to
`0effbf98fd1f4521313ba19f2378089ba7b3baf8`, the revision the running package
was built from (Home `main` is now `790f59e`; only tests/docs changed after
`0effbf9`, so runtime code is identical). Variable only: no installer, package,
runner, task, credential, Prometheus or Standard change.

- Old value saved first to
  `C:\ProgramData\HermesHome\backups\home-0effbf9-20261005\deployment-revision-before.json`
  (SHA-256 `f193a0838761638027a85b1afda43c6795b3aa11214d6cb464a193a9349ccc6f`;
  the file holds the old value, variable name, scope and UTC time). Rollback
  is
  `[Environment]::SetEnvironmentVariable('HERMES_HOME_DEPLOYMENT_REVISION','<old>','Machine')`.
- The other 17 `HERMES_HOME*` machine variables are unchanged (0 differences
  against the `machine-settings.json` backup; 18 variables before and after).
- **No Home restart was performed, deliberately.** The Home runtime does not
  read this variable: `src/` contains no reference to it (only
  `load_settings` reads other `HERMES_HOME_*` names), `run.ps1` does not set it,
  and the startup `provenance_header` carries `source_revision: null` with
  `provenance_status: unverified` because Home is not given a revision.
  Restarting would therefore not make it appear in diagnostics, and live
  `prompt_submit` traffic was present in the operational JSONL, so the running
  process (pid 14528, started 2026-10-05 12:00:36) was left untouched.
- The variable's actual consumer is the host-side live-gate attestation:
  `provenance\deployment.json` and `provenance\traces\*.json` carry a signed
  `deployment_revision` of `376583d…` (written 2026-09-17/23, signer
  `android-live-home-gate-20260917`). Those signed historical artifacts were
  not modified (re-signing needs the live-gate signing key and a new gate run),
  so they still attest the old identifier; a future attestation run on the host
  will pick up the corrected value.
- Post-change checks: task `Hermes Home` Running as SYSTEM, last start
  2026-10-05 12:00:36, result `267009`; `127.0.0.1:8780` and `127.0.0.1:8766`
  both on pid 14528; `/pair` 200; authenticated `/api/v1/diagnostics/status`
  200 and `/api/v1/configuration` 200 (admin credential read only inside the
  remote process, never printed); Prometheus `up{job="hermes-home"}` = `1`;
  runner SHA-256 `52b9316a…cc55` and exported task XML SHA-256 `f61e73a2…d495`
  unchanged; `diagnostics\home.jsonl` 147 records, 0 invalid, only marker hit
  is the benign `operation` label `prompt_submit`.

## CaticornQueen deployment — 2026-10-06 (f1eeb94, PR #80)

Deployed merged Home `origin/main` revision
`f1eeb94cab166131c0305b0543d37357c376b8d4` (#80, Android client connection
report platform acceptance) to CaticornQueen from detached worktree
`.worktrees/deploy-pr80`. The runtime diff from `0effbf9` is exactly
`src/hermes_home/observability/client_reports.py` (19 insertions, 3 deletions).
Package-only cutover: no installer, runner/task re-registration, dependency,
Prometheus, Standard, tailnet, machine-variable, or credential change. No
prompt was sent; verification used authenticated/read-only HTTP GETs and a
synthetic local validator check only.

### Build

- Exact worktree revision `f1eeb94cab166131c0305b0543d37357c376b8d4`:
  `uv run --python 3.14 --extra dev pytest -q` — 1,024 passed, four
  deprecation warnings; `uvx ruff check src tests` — passed;
  `uvx ruff format --check src tests` — 74 files already formatted.
- Wheel `hermes_relay_home-0.1.0-py3-none-any.whl`, SHA-256
  `1b075e82ac92ac6ded43eff8b7be67ca361a6c34a19537a546f4d8151e1ff31b`
  (39 members, 34 Python sources). Every Python source in the wheel is
  byte-identical to `git show f1eeb94:src/<path>`; only
  `hermes_home/observability/client_reports.py` differs from `0effbf9`.
  The wheel SHA-256 matched on CaticornQueen.

### Pre-flight and backup

- Before cutover, `Hermes Home` was Running as SYSTEM (last result `267009`);
  listeners `127.0.0.1:8780` and `127.0.0.1:8766` shared pid `14528`. All 34
  installed Python sources matched `0effbf9`. The host was observed without a
  diagnostic event for 138 seconds before stopping the task.
  `/pair`, authenticated `/api/v1/diagnostics/status`, and authenticated
  `/api/v1/configuration` returned 200; Prometheus
  `up{job="hermes-home"}` returned `1`.
- Protected backup directory
  `C:\ProgramData\HermesHome\backups\home-pr80-20261006` (SYSTEM and
  Administrators Full, deployment account Full):
  - `installed-package.zip` contains the 44 venv-relative package files;
    SHA-256 `d3b8a48244f81cc9c0470bc4e0290ea562715f48a0ca399652e547658cc8481c`,
    with `installed-package-manifest.json` and
    `installed-distributions.json`.
  - `home-preupgrade.sqlite3` was made with `sqlite3.Connection.backup` while
    the live database reported `journal_mode=delete`; `PRAGMA integrity_check`
    returned `ok`. Size 4,026,368 bytes; SHA-256
    `d94a25e9cf36931b3e9f5d92b5f0813e32b276f5019e869886dc445b0bd36922`.
  - Includes `run.ps1`, exported `task.xml`, `machine-settings.json` (18
    variables), `preflight-hashes.json`, and the deployed wheel.
  - `rollback-package.ps1` SHA-256
    `175abf0d3ba8140e0aa9244b806e64074b4ee837424251acf55b5ce3a82a9a51`,
    parsed by Windows PowerShell 5.1 with 0 parse errors; **not executed**.
    It stops only Home, waits for both listeners to release, validates the
    prior package archive checksum and manifest, makes a new online SQLite
    backup before restoring the `0effbf9` package, verifies restored hashes,
    runner and task definition, then starts Home. It leaves the database and
    machine settings unchanged.

### Cutover and live acceptance

`Stop-ScheduledTask 'Hermes Home'`; both listeners released;
`uv pip install --python C:\ProgramData\HermesHome\venv\Scripts\python.exe
--no-deps --force-reinstall <deploy-pr80>\hermes_relay_home-0.1.0-py3-none-any.whl`;
`Start-ScheduledTask 'Hermes Home'`. Only the Home scheduled task was stopped
and restarted. PowerShell's native-command error preference was disabled for
the `uv` call; it exited 0 and installed the staged wheel.

| Check | Result |
| --- | --- |
| Installed sources | All 34 installed Python source hashes equal the wheel and `git show f1eeb94:src/<path>`; relative to the previous package only `hermes_home/observability/client_reports.py` changed. Distributions unchanged: cffi 2.1.1, cryptography 50.0.1, hermes-relay-home 0.1.0, pycparser 3.0, segno 1.6.6, websockets 17.1. |
| Task / listeners | `Hermes Home` Running as SYSTEM, last start 2026-10-06 17:59:04 (host time), result `267009`. Both loopback listeners share new process pid `34736` (started 17:59:04). |
| HTTP | `/pair`, authenticated `/api/v1/diagnostics/status`, and authenticated `/api/v1/configuration` returned 200. Configuration response SHA-256 was unchanged from pre-cutover: `d2e71cb9ea5cab5800f11ecc49c92e3f2cf1c81e6fb32709c3f60a59b9721af3`. Admin credential was read and used only inside the remote process; it was never printed. |
| Prometheus | `up{job="hermes-home"}` returned `1`. |
| Functional behavior | Using the installed venv package, synthetic schema-1 Android report `{platform: "android", model: "Pixel 9 Pro XL"}` was accepted; `{platform: "windows"}` was rejected. No report was uploaded. |
| Preserved | All 18 `HERMES_HOME*` machine variables matched the backup (0 differences); `HERMES_HOME_DEPLOYMENT_REVISION` remains the previously corrected `0effbf98fd1f4521313ba19f2378089ba7b3baf8`. Exported task XML SHA-256 unchanged (`f61e73a28e55c7498e3f5f4db5b42141f7e0d67f35b755fde75a4c7a5ee2d495`); runner SHA-256 unchanged (`52b9316aac157fcc3095373cd8231083841b1c864c0eb7b6e88135484985cc55`). Diagnostics ACL remains SYSTEM Modify and Administrators Read. |
| Operational JSONL | New `diagnostics\home.jsonl` contains two parsed records (`provenance_header`, `process_started`), 0 invalid lines, and no credential/content marker hits. The provenance header has `source_revision: null` and `provenance_status: unverified`; the service is not given a revision at runtime. Older rotated files contain only the benign marker `prompt_submit` for prompt-related labels. |

No rollback was needed. Media-server, Standard, Qwen3 and iOS were not touched.

## CaticornQueen deployment — 2026-10-07 (b964082, PR #83)

Deployed merged Home `origin/main` revision
`b964082c034ea60c249678ce416db7015cb0a382` (#83: `session.interrupt` after the
text terminal releases the response-audio tail, and the acknowledgement is sent
only after the audio worker has released prompt admission) to CaticornQueen from
detached worktree `.worktrees/deploy-pr83`. The runtime diff from the previously
deployed `f1eeb94` is exactly `src/hermes_home/bridge/endpoint.py` and
`src/hermes_home/bridge/standard.py` (46 insertions, 4 deletions); #81 (ops
patches under `deploy/ops/`) changes no runtime source and was not applied.
Package-only cutover: no installer, runner/task re-registration, dependency,
Prometheus, Standard, tailnet, machine-variable, or credential change. No prompt,
claim, or Pixel action was performed.

### Build

- Gates at the exact revision: `uv run --python 3.14 --extra dev pytest -q` —
  1,039 passed, four deprecation warnings; `uvx ruff check src tests` — passed;
  `uvx ruff format --check src tests` — 75 files already formatted.
- Wheel `hermes_relay_home-0.1.0-py3-none-any.whl`, SHA-256
  `851c59bc739053cb53eb9fdd3d29153212ec39f26c4ac36e6c27146f4fe0deeb` (39
  members, 34 Python sources), byte-identical to `git show b964082:src/<path>`;
  only the two files above differ from `f1eeb94`. The SHA-256 matched on the host.

### Pre-flight and backup

- Resolved before action: running package = `f1eeb94` (all 34 installed sources
  matched), task Running as SYSTEM since 2026-10-06 17:59:04, both loopback
  listeners on pid `34736`, 18 `HERMES_HOME*` machine variables,
  `HERMES_HOME_DEPLOYMENT_REVISION` still `0effbf98…` (unchanged by design: the
  runtime does not read it), task XML SHA-256 `f61e73a2…d495`, runner SHA-256
  `52b9316a…cc55`, `/pair` and authenticated diagnostics/configuration 200,
  Prometheus `up{job="hermes-home"}` = 1. The cutover waited until no Home
  diagnostic event had occurred for more than 130 seconds.
- Protected backup directory
  `C:\ProgramData\HermesHome\backups\home-pr83-20261007` (SYSTEM,
  Administrators, deployment account):
  - `installed-package.zip` (44 files) SHA-256
    `ee01927e51be824e781ede4b0dac351b5927c1cb6ef5f358a56edbaa5c720bf2`, with
    manifest and `installed-distributions.json`.
  - `home-preupgrade.sqlite3`: online `sqlite3.Connection.backup` (live DB
    `journal_mode=delete`), `PRAGMA integrity_check` `ok`, 4,259,840 bytes,
    SHA-256 `3ebb032dd7cd5e47812fb93ce9769f2c9b09ff2720bd33c2f3b9bdd65133e129`.
  - `run.ps1`, `task.xml`, `machine-settings.json` (18 variables),
    `preflight-hashes.json`, and the deployed wheel.
  - `rollback-package.ps1` SHA-256
    `da19df3d8b744a08569a37346b31bf9fcb5056e07fbe239d21029700f4d8372c`, parsed
    by Windows PowerShell 5.1 with 0 errors; **not executed**. It stops only
    Home, waits for both listeners to release, validates the archive checksum
    and manifest, takes a new online SQLite backup, restores the `f1eeb94`
    package files with hash verification, checks the runner and task
    definition, and starts Home. It does not restore the database or machine
    settings.

### Cutover and live acceptance

`Stop-ScheduledTask 'Hermes Home'`; both listeners released;
`uv pip install --python C:\ProgramData\HermesHome\venv\Scripts\python.exe
--no-deps --force-reinstall <deploy-pr83>\hermes_relay_home-0.1.0-py3-none-any.whl`
(native-command error preference relaxed; exit 0); `Start-ScheduledTask
'Hermes Home'`. Only that task was stopped and started. Staging directory:
`C:\Users\achap\hermes-home-deploy\deploy-pr83\` (earlier staging directories
and wheels untouched).

| Check | Result |
| --- | --- |
| Installed sources | All 34 installed Python source hashes equal the wheel and `git show b964082:src/<path>`; `_unmark_interrupted` (endpoint) and `terminal_audio_tail` (standard) are present in the installed package. Distributions unchanged (cffi 2.1.1, cryptography 50.0.1, hermes-relay-home 0.1.0, pycparser 3.0, segno 1.6.6, websockets 17.1). |
| Task / listeners | `Hermes Home` Running as SYSTEM, last start 2026-10-07 12:32:29 (host time), result `267009`; `127.0.0.1:8780` and `127.0.0.1:8766` on the same new process, pid `33944`. |
| HTTP | `/pair`, authenticated `/api/v1/diagnostics/status`, and authenticated `/api/v1/configuration` returned 200; configuration response SHA-256 unchanged from pre-cutover (`d2e71cb9…1af3`). The admin credential was used only inside the remote process and never printed. |
| Prometheus | `up{job="hermes-home"}` = 1. |
| Profiles / claims path | Configuration lists profiles `amanda`, `jensen`, `spark` (shared), 1 wake mapping, 1 device, 1 room; the Standard gateway host (`media-server`, port 8443) accepts a TCP connection from CaticornQueen. Through the tailnet front door `/pair` returned 200 and `/api/v1/client-claims` returned 401 (route reaches Home; auth enforced). Tailscale Serve routes unchanged. |
| Preserved | 18 `HERMES_HOME*` machine variables equal the backup (0 differences); task XML SHA-256 `f61e73a2…d495` and runner SHA-256 `52b9316a…cc55` unchanged; diagnostics ACL unchanged (SYSTEM Modify, Administrators Read). |
| Operational JSONL | New `diagnostics\home.jsonl`: two parsed records (`provenance_header`, `process_started`), 0 invalid, no credential/content marker hits; header has `source_revision: null`, `provenance_status: unverified`. Rotated files show only the benign `prompt_submit` label. |

No rollback was needed. The effect of #83 on a real Pixel interrupt turn is not
observed by this deployment; that is the separate live Android gate.
