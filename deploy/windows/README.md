# Windows deployment

The household Prometheus instance runs on CaticornQueen as a native Windows
service. This bundle installs the Home wheel into a Python 3.14 virtual
environment, runs it as a SYSTEM scheduled task, stores the admin credential in
an ACL-protected file, and adds an authenticated `hermes-home` scrape job to
Prometheus.

The installer defaults to loopback on port `8780` because CaticornQueen already
uses port `8765` for the Qwen TTS service. The sibling bridge listener defaults
to loopback on port `8766`; its bind host, port, and non-secret route label are
set by the installer as well. Prometheus scrapes the Home process locally; LAN
binding remains a separate, deliberate trust-boundary decision.

## Build and copy

From the repository root on macOS or Linux:

```sh
uv build --wheel
scp dist/hermes_relay_home-*.whl deploy/windows/install.ps1 deploy/windows/run.ps1 \
  CaticornQueen:C:/Users/achap/hermes-home-deploy/
```

Install `uv` on CaticornQueen, open an elevated PowerShell session, and run:

```powershell
Set-Location C:\Users\achap\hermes-home-deploy
$wheel = Get-ChildItem .\hermes_relay_home-*.whl | Select-Object -First 1
.\install.ps1 -WheelPath $wheel.FullName
```

For the tailnet front-end test route, keep the bridge loopback-bound and give
the route an explicit operator label:

```powershell
.\install.ps1 -WheelPath $wheel.FullName `
  -BridgeBindHost 127.0.0.1 -BridgePort 8766 `
  -BridgeRouteId caticornqueen-tailnet
```

The installer persists these values as the machine environment variables
`HERMES_HOME_BRIDGE_BIND_HOST`, `HERMES_HOME_BRIDGE_PORT`, and
`HERMES_HOME_BRIDGE_ROUTE_ID`. To expose only the versioned WebSocket path
through Tailscale Serve, configure the path separately on the host:

```powershell
tailscale serve --bg --https=443 `
  --set-path=/api/v1/bridge/ws `
  http://127.0.0.1:8766/api/v1/bridge/ws
```

The target repeats the bridge path because Tailscale strips the public
`--set-path` prefix before proxying. This command adds the bridge handler and
preserves an existing root handler. Serve remains tailnet-only unless Funnel
is explicitly enabled. The deployment wiring makes the listener reachable;
the console runtime still returns `hermes_unavailable` until a HomeBridge
factory is supplied.

To enable paired mode, create the operator-owned 64-character hexadecimal
root-secret file first and pass it explicitly:

```powershell
.\install.ps1 -WheelPath $wheel.FullName -CredentialRootSecretFile C:\ProgramData\HermesHome\secrets\credential-root
```

The installer never creates that root secret. Without it, and without the
legacy `-DeviceCredentialsFile` option, endpoint authentication remains
disabled. The two credential modes cannot be supplied together.

## Standard-backed bridge

The Home route uses one dedicated Standard gateway target. Home creates each
conversation claim after a device wins arbitration and stores the claim and
its Standard Session binding in the Home SQLite database. There is no separate
conversation-grant file.

Create the Standard server token on the Standard host, create the paired Home
root secret on CaticornQueen, and pass the gateway and token settings together:

```powershell
.\install.ps1 -WheelPath $wheel.FullName `
  -CredentialRootSecretFile C:\ProgramData\HermesHome\secrets\credential-root `
  -StandardGatewayUrl 'wss://media-server.<tailnet>/api/ws' `
  -StandardTokenFile C:\ProgramData\HermesHome\secrets\standard-token
```

`HERMES_HOME_PROXY_DIAGNOSTIC_LINK=1` is an explicit Home opt-in and is disabled
by default. Leave it unset for a direct Standard destination such as the
`wss://media-server.<tailnet>/api/ws` target above; direct Standard receives no
diagnostic correlation header. Set it to `1` only when Home connects to the
local proxy at exactly `ws://127.0.0.1:9121/api/ws`. On Windows, set this in the
machine environment and restart the Hermes Home task for it to take effect:

```powershell
[Environment]::SetEnvironmentVariable('HERMES_HOME_PROXY_DIAGNOSTIC_LINK', '1', 'Machine')
```


The Standard token file is read by the Home process and never placed in an
endpoint response. Home checks the paired Device credential and the exact
Wake Mapping grant, then opens an independent Standard Session for the resolved
Profile. The default idle timeout is 8 seconds after playback completion; set
`-ConversationIdleTimeoutSeconds` to change it.

The gateway and token settings are all-or-nothing. Removing them on a later
install clears the machine environment and returns the route to the safe
unavailable bridge. The route remains tailnet-only; a connected physical
Android device and an approved live Profile are still required for the final
audio/reconnect proof.

The installer is idempotent: it preserves the existing admin token, replaces only its marked
Prometheus job, validates the candidate configuration with `promtool`, saves a
timestamped backup, and restarts the Prometheus service.

The resulting process is managed by the `Hermes Home` scheduled task. Its data,
logs, virtual environment, and secret live beneath
`C:\ProgramData\HermesHome`. The installer waits for both `/metrics` and an
`up{job="hermes-home"}` Prometheus result before returning success.

The installer preserves the legacy `C:\ProgramData\HermesHome\logs` directory
(or `<InstallRoot>\logs` when `-InstallRoot` is changed) separately from the
restricted operational diagnostics directory at
`C:\ProgramData\HermesHome\diagnostics` (or `<InstallRoot>\diagnostics`). The
installer creates the diagnostics directory and applies a protected DACL:
SYSTEM has Modify access and BUILTIN\Administrators have Read access; no
inherited or other access rules are retained. Existing `logs` contents and ACLs
are not used for the operational sink or modified by this ACL operation.

The installer persists the diagnostics path as the machine
`HERMES_HOME_DIAGNOSTICS_DIR` environment variable, and `run.ps1` explicitly
overrides it for the Home process with the same `<InstallRoot>\diagnostics`
path. The per-process setting keeps runner behavior aligned with the directory
whose ACL the installer protects, even if a different machine value was set.
The single JSONL sink writes `home.jsonl` plus four rotated backups (10 MiB
active, 50 MiB maximum) and expires files after 14 days. Records are limited
to 2 KiB and a nonblocking 1,024-record queue drops newest when full. Loss
counters are process-local status; best-effort JSONL loss snapshots are
emitted at most once per minute after losses accumulate. Total sink failure or
a crash can leave counters unavailable. A partial final line is a gap, not
complete evidence.

Operational JSONL files are local-only; no file-serving or upload route is
added. Existing authorized Home diagnostics review and opted-in client-report
paths are unchanged. Authorized operators can read the files locally, for
example:

```powershell
Get-Content C:\ProgramData\HermesHome\diagnostics\home.jsonl
```

`run.ps1` does not append stdout/stderr to a second unbounded log. It launches
Home with native stderr isolated from PowerShell's terminating error handling
and propagates the process exit code; operational records go only to the
bounded JSONL sink.


## Diagnostics export (optional)

The installer does not enable the export. To turn it on, create an export
directory with the same protected DACL as the diagnostics directory (SYSTEM
Modify, BUILTIN\Administrators Read, nothing inherited), for example
`C:\ProgramData\HermesHome\diagnostics\export`, and set the machine
environment variable `HERMES_HOME_EXPORT_DIR` to it (and optionally
`HERMES_HOME_EXPORT_INTERVAL_SECONDS`). Machine variables reach the task
process only when the task starts, so restart the Hermes Home task afterwards.
Verify the ACL with `icacls` before relying on it. Files,
retention (14 days safe events, 7 days client reports) and status fields are
described in [`../../observability/README.md`](../../observability/README.md).

[`hermes-home-export.alloy`](hermes-home-export.alloy) is the Alloy configuration
(`loki.source.file` to `loki.process` to `loki.write`) that ships the export to
Loki. It is validated with Grafana Alloy v1.20.1 (`alloy fmt` leaves it
unchanged and `alloy validate` passes); it uses `sys.env`, because the bare `env`
function is deprecated in that release and fails validation. Installing it is a
host step, separate from the Home package. The household Loki has no
authentication and no tenant, so the push URL is the only setting:
`HERMES_HOME_LOKI_PUSH_URL=http://ops.taila59979.ts.net:3100/loki/api/v1/push`
(this needs the tailnet grant from the Home host's tag to ops `tcp:3100`).

Install with the official release from the Grafana Alloy GitHub releases page:
verify `SHA256SUMS`, run the silent installer with `/S /CONFIG=<config path>
/DISABLEREPORTING=yes /USERNAME="NT SERVICE\Alloy"` (a virtual service account,
no password; never the default LocalSystem), then check these three things:

- **Set the service environment in the registry, not with `/ENVIRONMENT`.** In
  v1.20.1 the installer truncated the URL at `//` (stored `...=http:`). Set
  `HKLM:\Software\GrafanaLabs\Alloy` value `Environment` (multi-string) to the
  full `HERMES_HOME_LOKI_PUSH_URL=...` line and restart the `Alloy` service.
- **Grant read on the export directory only**, after the service exists:
  `icacls <export dir> /grant "NT SERVICE\Alloy:(OI)(CI)(RX)"`. Grant nothing on
  `HermesHome\`, `diagnostics\` or `secrets\`. Give the account Modify on
  `C:\ProgramData\GrafanaLabs\Alloy\data` (positions file) only.
- **Verify** on `http://127.0.0.1:12345` (the UI binds loopback only) that
  `loki.source.file` and `loki.write` are healthy, `loki_write_sent_entries_total`
  rises and `loki_write_dropped_entries_total` stays 0. Wiping the Alloy data
  directory resets positions and re-ingests the export files, duplicating lines in
  Loki. To roll back, stop and uninstall the `Alloy` service
  (`%PROGRAMFILES%\GrafanaLabs\Alloy\uninstall.exe /S`) and remove the ACL entry;
  the local JSONL files stay the source of truth.

## Personal-client pairing (HOME-NW-17)

TUI, iOS, macOS, and Android clients pair from the Home pairing page and then reach
Home over the tailnet. Publish only the pairing page and the device-facing
routes; admin routes (offers, request listing, approval, rotation,
configuration, metrics, diagnostics) stay loopback-only and are reached only
through the signed-in page. Each path repeats in the target because Tailscale
strips the `--set-path` prefix:

```powershell
foreach ($path in '/pair', '/api/v1/enrollment/requests', '/api/v1/client-claims',
                  '/api/v1/client-sessions', '/api/v1/profile-grants', '/api/v1/devices',
                  '/api/v1/client-diagnostics') {
  tailscale serve --bg --https=443 --set-path=$path "http://127.0.0.1:8780$path"
}
```

`/api/v1/enrollment/requests` also serves the admin-only request listing and
approval. Home refuses every admin-token route on a proxied request
(`admin_local_only`), so through Serve only the signed-in pairing page can
approve; the admin API stays usable on loopback.
`/api/v1/devices` carries device configuration and self-renewal, which require
the device's own credential. The bridge route stays as configured above.

Open `https://<home tailnet name>/pair`, sign in with the admin token, and
choose **Create pairing code**. Profiles marked `shared` in configuration can
be granted to any device; another Profile's second and later devices wait for
approval from a client already paired to that Profile.

`-ClientClaimsPerDevice` (default 8) caps concurrent client conversations per
device, and `-ClientReconnectGraceSeconds` (default 120) is how long a
disconnected client keeps its conversation before Home closes it. Both are
written with the Standard settings and cleared with them.


## Opt-in client connection reports

Publish `/api/v1/client-diagnostics` through the existing tailnet Serve route when deploying the client-report slice. This route only accepts authenticated Device uploads; it does not expose a report-reading API. Sign in at `/pair` and use **Connection reports → Refresh reports** to review reports and recent Home events. Device reports expire seven days after receipt, with pruning on read/write. Settings in the Apple app controls per-Home consent; install the matching Apple build and enable it on the intended device. No credential changes are required.
