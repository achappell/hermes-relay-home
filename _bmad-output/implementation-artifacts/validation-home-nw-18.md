---
story: HOME-NW-18
status: deployed-verified-source
validated: 2026-10-03
baseline_commit: b77c830d0c282cee44cdcbbec5008ab754392e01
implementation_commit: d043192eaa4022689f59d78b3f3dca164dbb8dfc
---

# HOME-NW-18 validation record

## Verification boundary

Home mints a `claim_ref` for each client claim, lists a device's active
client claims (`GET /api/v1/client-claims`) and closes an explicit set of them
(`POST /api/v1/client-claims/close`). Deterministic tests cover the pilot
claim-leak scenario, active-state mapping, wall-clock timestamps, device
isolation, `not_open` answers, request validation, proxied requests,
configuration degradation, absence of Standard calls, credential denials,
identifier safety in logs/diagnostics, metrics, sticky close reasons,
concurrent close/create/open/reconnect, and NW-17 to NW-18 migration.

Bridge coverage includes mid-turn client close through a real `HomeBridge` and
`BridgeEndpoint` using fake Standard sockets (interrupt, close code 1000,
`stale_conversation`), close races during open/reconnect, the per-handle
revocation guard, detached-marker activity precedence, parking-lot mark/take/
expiry synchronization, and production `create_bridge_server` claim-store
wiring. API coverage verifies a closed session is listed inactive and can be
resumed using its durable session reference.

This validates the Home slice against fake Standard sockets and the production
SQLite claim store. A disposable runtime exercised actual Home HTTP and
`create_bridge_server` WebSocket listeners end-to-end against a controlled
local Standard-protocol WebSocket stand-in: authorized open, prompt submit,
disconnect/park, reconnect/adopt, HTTP close, terminal
`stale_conversation`/1000, repeat close, and inactive-but-preserved Session
listing with no prompt replay. This was not a live Standard Hermes gateway; no
household traffic, Tailscale Serve, iOS, or deployment was exercised. No
sibling repository was changed.

## Observed checks

| Check | Exact command | Result |
| --- | --- | --- |
| Python runtime | `uv run --python 3.14 --locked --extra dev python --version` | `Python 3.14.8` |
| Focused HOME-NW-18 and adjacent bridge/claim suites | `uv run --python 3.14 --locked --extra dev pytest -q tests/test_client_claim_store.py tests/test_client_claim_list_close_api.py tests/test_client_claim_close_bridge.py tests/test_bridge_server.py tests/test_runtime.py tests/test_client_claims_api.py tests/test_standard_bridge.py tests/test_bridge_endpoint.py` | `382 passed in 11.43s` |
| Runtime test file after removing forwarding-only assertion | `uv run --python 3.14 --locked --extra dev pytest -q tests/test_runtime.py` | `21 passed in 0.86s` |
| Full Home test suite | `uv run --python 3.14 --locked --extra dev pytest -q` | `818 passed, 2 warnings in 15.91s` |
| Ruff lint | `uvx ruff check src tests` | `All checks passed!` |
| Ruff format | `uvx ruff format --check src tests` | `73 files already formatted` |
| Lockfile check | `uv lock --check` | `Resolved 17 packages in 9ms` |
| Disposable two-device loopback runtime smoke | One-shot inline Python with `uv run --python 3.14 --locked --extra dev`; no script file persisted | Actual Home HTTP runtime on loopback with temporary SQLite/credentials. Two devices enrolled/authenticated and each created a claim. Device 1 listed only its own ref; close returned `closed`; subsequent list was empty; repeat close returned `not_open`; Device 2 continued listing its own claim. Temporary state removed. |
| Disposable loopback Home/bridge/Standard-peer lifecycle smoke | One-shot inline Python with `uv run --python 3.14 --locked --extra dev`; no script file persisted | Actual Home HTTP and `create_bridge_server` WebSocket listeners, temporary SQLite/credentials, and a controlled local Standard-protocol WebSocket stand-in (not live Hermes). Authorized open=`ready`; one prompt submitted; after disconnect list=`waiting_to_reconnect`; reconnect=`ready`, list=`replying`; HTTP close=`closed`; client WebSocket close=`1000 stale_conversation`; repeat close=`not_open`; session list retained one row with `active:false`, title `Smoke session`. Stand-in observed counts: `commands.catalog=1`, `session.create=1`, `prompt.submit=1`, `session.interrupt=1`, `session.list=1`; `session.resume=0`; no prompt replay. Temporary state removed. |
| Smoke warning | Inline smoke command | DeprecationWarning: connect() must be used as a context manager; alternatively use websocket = connect(..., legacy=True) to connect directly. |
| Whitespace/diff | `git diff --check` | Passed; no output |

## Deviations from the spec text

- The detached marker is set and cleared by the bridge server's parking lot
  (`park`, `take`, park expiry), not by `BridgeEndpoint.detach`/`adopt`; parking
  is where "parked" is defined, and the endpoint needs no store reference.
  `mark_detached` takes only the handle (handles are unique), and parking-lot
  notifications are serialized with entry insertion/removal.
- `create_bridge_server` takes an optional `claim_store`; it registers the
  parking lot's `evict` as a store close listener. Every notifying close
  (client close, device, grant or Profile revocation) evicts a parked endpoint.
- Only `client_closed` is terminal in the endpoint. Other stale authorization
  failures, including device/grant/Profile revocation and generation or
  configuration changes, retain the existing upstream-failure/reconnect path.
- `mark_open` refusing an inactive or expired claim yields `stale_conversation`
  (open and reconnect), matching the close-versus-open row.
- `create_client_claim` returns `(handle, claim_ref)`.
- Client-route diagnostic failure codes map onto the existing allowlist
  (`client_claim_unavailable` → `forbidden`, `claim_limit` and other denials →
  `claim_denied`, configuration migration → `conflict`); `client_sessions` is
  also a safe route ID.
- Close waits for serialized handlers, so it may take longer than 15 seconds;
  after a client timeout the client must re-list rather than assume the close
  did not happen.

## Review findings disposition

- **D1 — terminal close semantics:** Implemented only for `client_closed`;
  other stale authorization failures retain the pre-existing reconnect/upstream
  behavior. Parameterized bridge tests cover device/grant/Profile revocation and
  generation/configuration changes. A real local WebSocket close was exercised
  against a controlled protocol stand-in; live Standard Hermes remains unverified.
- **D2 — close timeout guidance:** Spec and v1 contract now explain serialized
  notification handlers may exceed 15 seconds and clients must re-list after a
  timeout. Household-scale latency was not measured.
- **P1 — parking marker race:** Marker set/clear callbacks are serialized with
  parking entry publish/take/expiry. Deterministic gated race tests cover park,
  take, and expiry. No cross-process marker behavior is claimed; markers are
  intentionally process-local.
- **P2 — runtime/parking integration:** A behavioral test closes a parked claim
  through the store and confirms the parking entry is evicted. Disposable
  runtime smoke exercised actual HTTP and WebSocket listeners through parking,
  adoption, bound HTTP close, interrupt, and terminal client disconnect using a
  controlled local Standard-protocol stand-in. It is not live Hermes.
- **P3 — close/reopen races:** Tests cover close against open/reconnect and
  `mark_open` on inactive/expired rows. These use controlled fake upstream
  sockets, not live Standard.
- **P4 — diagnostics contracts:** Safe `client_sessions` route ID and diagnostic
  aliases, including configuration-migration conflict, are pinned by API tests.
- **P5 — identifier leakage:** Captured logs/diagnostics are checked for private
  refs/handles/session/grant identifiers in live-close, parked-close, and
  interrupt-failure cases. This covers observed test output only.
- **P6 — edge-case coverage:** Tests cover detached-marker precedence for all
  active activities, Room wake/touch exclusion, API close/list/resume behavior,
  migration degradation, store failures, and repeated migration. Runtime smoke
  confirms end-to-end authenticated list/close/isolation behavior for two
  temporary devices.

Remaining verification boundary: no live household traffic, Standard Hermes,
Tailscale Serve, iOS client, deployment, or host-sleep behavior was exercised.

## Not verified

Live Standard Hermes interrupt behavior, Tailscale Serve routing, iOS
consumption, deployment, and host-sleep behavior were not exercised. The local
Standard protocol peer is a controlled stand-in; it does not validate a real
Hermes gateway's behavior.

## Deployment to CaticornQueen — preflight (pending host access)

Recorded during the authorized deployment attempt; no remote change has been
made yet. Secret-safe: paths, hashes and states only.

- **Reviewed revision:** `082e5938615ae4fc037ed6b3a1e791ccda04bd89`
  (PR #69 merge commit; `git ls-remote origin refs/heads/main` matched it
  exactly, with no newer commits on `main`). CI and release-please workflows
  both succeeded on that SHA.
- **Build:** isolated gitignored worktree `.worktrees/nw18-deploy` pinned to
  that SHA; `uv build --wheel` produced
  `/tmp/hermes-home-nw18-082e593/hermes_relay_home-0.1.0-py3-none-any.whl`,
  SHA-256 `42eecc57e9c655d1eba3ed39b3ad61c5da0264d6d60bbd051777e12eeca9349b`.
  The wheel packages 33 `hermes_home` source files and contains the NW-18
  `GET /api/v1/client-claims`, `POST /api/v1/client-claims/close` dispatch,
  the `claim_ref`/`created_wall_at`/`opened_at` migration, and the startup
  `service_restart` claim close.
- **Target:** CaticornQueen (`100.78.105.19`, Windows); Home lives under
  `C:\ProgramData\HermesHome` and runs as the `Hermes Home` scheduled task.
- **Service state:** untouched. No authenticated session was established, no
  wheel was copied or installed, no task was stopped or restarted, and no
  files were written on the host.
- **Access prerequisite (blocking):** the dedicated key
  `~/.ssh/id_ed25519_caticornqueen` (alias `Host caticornqueen`, user `achap`)
  has not yet completed successful authentication; server-side key
  installation and permissions remain unverified. The user reported a
  `too many auth failures` message on the target — recorded as a report, not
  a diagnosis. Future connections must use
  a single explicit-identity command:
  `ssh -o IdentitiesOnly=yes -o IdentityAgent=none -i ~/.ssh/id_ed25519_caticornqueen caticornqueen "<command>"`.
  No account unlock or sshd change is authorized.
- **Plan once access works (from `deploy/windows/README.md` and the NW-17 /
  title-event deployment boundaries):** narrow package-only upgrade — inspect
  task/settings state, back up the installed package and take a consistent
  SQLite backup (WAL checkpoint) under `C:\ProgramData\HermesHome\backups\`,
  `uv pip install --python C:\ProgramData\HermesHome\venv\Scripts\python.exe
  --no-deps --force-reinstall <wheel>`, restart only the `Hermes Home`
  scheduled task, then verify hashes, `/pair`, authenticated `/metrics`,
  Prometheus `up{job="hermes-home"}`, and the new claim routes. Existing
  root-secret/admin/device/Standard credentials, grace/limit settings,
  listener and tailnet settings stay untouched; the installer must not be
  invoked with omitted settings.

## Deployment to CaticornQueen — completed 2026-10-03

This section supersedes the pending-access state above and the earlier
deployment verification exclusion. The local-test and live-Standard boundaries
remain distinct. Access succeeded after the user's SSH fix, using the dedicated
key with `IdentitiesOnly=yes`, `IdentityAgent=none`, and `BatchMode=yes`; no key,
account, or sshd settings were changed.

### Artifact and preserved runtime

- Deployed **`082e5938615ae4fc037ed6b3a1e791ccda04bd89`**, not a newer `main`.
  The existing isolated worktree HEAD matched this SHA. Each of the wheel's 33
  Python source files was byte/hash-compared with `git show <SHA>:src/<path>`.
- Wheel: `hermes_relay_home-0.1.0-py3-none-any.whl`; local and copied remote
  SHA-256 both matched
  `42eecc57e9c655d1eba3ed39b3ad61c5da0264d6d60bbd051777e12eeca9349b`.
  Remote ZIP integrity passed. After installation, all 33 installed sources
  and the complete source-file set matched the reviewed wheel exactly.
- Existing runtime: Python **3.14.7**, distribution version **0.1.0**, SYSTEM
  scheduled task `Hermes Home`, running the existing
  `C:\ProgramData\HermesHome\run.ps1`. No installer, dependency upgrade,
  task re-registration, Prometheus restart, or tailnet route command was used.
- All **18** `HERMES_HOME*` machine variables and the exported task definition
  matched the pre-upgrade snapshots. Runner, existing credential files, and
  Prometheus configuration hashes remained unchanged; the Home configuration
  API response and installed distribution names/versions also matched.
  Listeners remain `127.0.0.1:8780` and `127.0.0.1:8766`; claim limit **8**,
  reconnect grace **120 seconds**, idle timeout **8 seconds**, Standard gateway,
  and credential paths were preserved.
- The pre-existing `HERMES_HOME_DEPLOYMENT_REVISION` variable was deliberately
  preserved with the other settings; it still contains
  `376583d7273e08a090d7ec33c416e0b32880a343`. It is not proof of this package's
  revision. The exact installed source comparison and
  `deployment-receipt.json` identify this deployment.

### Backups and upgrade commands

ACL-protected rollback directory (SYSTEM, Administrators, and the deployment
account only):

`C:\ProgramData\HermesHome\backups\nw18-082e593-20261003`

- `installed-package.zip`: all **43** prior installed-distribution files,
  including metadata and console entry points, with venv-relative paths.
  SHA-256:
  `2493dabeee2464dd978d0d2abfc64190395a213dc18a8004552006525726ebd7`.
  `installed-package-manifest.json` records every backed-up file hash.
- `home-preupgrade.sqlite3`: consistent SQLite online backup made with
  `sqlite3.Connection.backup`, not a raw database-file copy. This API includes
  committed WAL contents safely; the live database actually reported
  `journal_mode=delete`. Backup `PRAGMA integrity_check` returned `ok`.
  Size **3,190,784 bytes**, SHA-256:
  `44f9c19dc062f4c1cb3a8f7d82e16d2fbbd7947345a312f6dd108040de00467b`.
- Retained `task.xml`, `machine-settings.json`, `run.ps1`,
  `configuration.json`, `preserved-file-hashes.json`,
  `installed-distributions.json`, the reviewed wheel, and
  `deployment-receipt.json`. No credentials were printed or copied into this
  validation note.

After healthy baseline `/pair`, authenticated `/metrics`, and Prometheus
`up=1`, the package-only cutover used:

```powershell
Stop-ScheduledTask -TaskName 'Hermes Home'
# Confirmed both Home listeners released before replacing the package.
& C:\Users\achap\AppData\Local\Microsoft\WinGet\Links\uv.exe pip install `
  --python C:\ProgramData\HermesHome\venv\Scripts\python.exe `
  --no-deps --force-reinstall `
  C:\ProgramData\HermesHome\backups\nw18-082e593-20261003\hermes_relay_home-0.1.0-py3-none-any.whl
Start-ScheduledTask -TaskName 'Hermes Home'
```

Installation and restart commands exited successfully. Only this scheduled
task was stopped/started. Existing active claims close on service restart by
design; no real user's claim was separately submitted to the close API.

### Observed live verification

| Check | Observed result |
| --- | --- |
| Task/listener stability | `Running`; last start `2026-10-03T20:50:19-05:00`; the same listener PID **18124** and task start time persisted across a **35-second** observation. Last task result **267009** (`0x41301`, task currently running). Both loopback listeners were present. |
| Pairing page | Local HTTP `GET /pair` returned **200** before and after upgrade. |
| Authenticated metrics | Local HTTP `GET /metrics` with the existing admin bearer token returned **200**, containing Home metrics. Token read only within the remote process and never emitted. |
| Home configuration | Authenticated `GET /api/v1/configuration` returned **200**, exactly matching the pre-upgrade configuration. |
| Prometheus | `/api/v1/targets` reported the `hermes-home` target **up**, empty `lastError`, last scrape `2026-10-03T20:54:36.1635009-05:00`; the `up{job="hermes-home"}` query returned **1**. |
| Actual NW-18 lifecycle | Through the existing loopback admin enrollment/approval/consume APIs, enrolled **two disposable QA TUI devices**, each granted only an existing available shared Profile. Each created a new, unopened client claim. Authenticated `GET /api/v1/client-claims` returned only that device's own claim, state **connecting**, `max_claims=8`. |
| Isolation and close | QA device 1 attempting to close QA device 2's ref returned **not_open**. Device 1 closing its own ref returned **closed**; re-list was empty; repeat close returned **not_open**. Device 2's claim remained listed and unchanged, then its own close returned **closed** and re-list was empty. |
| QA cleanup | Both disposable devices were revoked through the admin API, and each credential subsequently received **401** from claim listing. Both QA claims had already been closed. Credentials existed only in the one-shot remote process; no smoke scripts or credential files were persisted. Revoked enrollment/device and closed-claim audit records remain in SQLite; no direct deletion or household configuration rewrite was performed. |

Final package/configuration/health verification timestamp:
**2026-10-04T01:54:37.965731Z** (October 3 on the Windows host).

An initial settings-count check used an unsuitable PowerShell collection
`.Count` expression and stopped with `Environment key count changed` before
any mutation. Repeating that comparison with
`@($before.PSObject.Properties).Count` observed **18 before / 18 after** and
all values equal. This was a verification-script issue, not a settings change.

### Rollback and remaining boundaries

No rollback was needed. A prepared, **not executed** package rollback script is
retained alongside the assets:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  C:\ProgramData\HermesHome\backups\nw18-082e593-20261003\rollback-package.ps1
```

It stops only Home, requires both listeners to release, validates the archived
package against its manifest, saves a new consistent
`home-before-rollback-<UTC>.sqlite3`, restores the old Home package/metadata and
entry points, verifies restored hashes, and starts the unchanged task.
It deliberately **does not replace the live database** or machine settings:
post-deployment user data must not be discarded by blindly restoring the older
SQLite snapshot. Rollback execution/old-code compatibility with the current
database has not been exercised; after any rollback, verify the task, pairing,
authenticated metrics, and Prometheus again.

Live feature evidence is the authenticated HTTP create/list/close/isolation
exercise above, not inference from health. No QA bridge connection, Standard
Session creation, Hermes prompt, or real user's close request was submitted.
Live Standard interruption, active/reconnecting WebSocket close behavior,
tailnet/Tailscale Serve end-to-end routing, iOS consumption, and host sleep were
not exercised in this deployment. No Vaults/iOS files, commits, or pushes were
made; the existing worktrees and prior uncommitted validation section were
preserved.
