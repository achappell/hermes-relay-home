# Windows deployment

The household Prometheus instance runs on CaticornQueen as a native Windows
service. This bundle installs the Home wheel into a Python 3.14 virtual
environment, runs it as a SYSTEM scheduled task, stores the admin credential in
an ACL-protected file, and adds an authenticated `hermes-home` scrape job to
Prometheus.

The installer uses port `8780` because CaticornQueen already uses port `8765`
for the Qwen TTS service. A first install binds Home to loopback; later runs
keep whatever bind host is already configured (see
[Bind host and metrics scrape](#bind-host-and-metrics-scrape)). The sibling
bridge listener defaults to loopback on port `8766`; its bind host, port, and
non-secret route label are set by the installer as well. The local Prometheus
job always scrapes `127.0.0.1:8780`; exposing Home beyond loopback is a
separate, deliberate trust-boundary decision.

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

## Bind host and metrics scrape

Home reads its listen address from the machine environment variable
`HERMES_HOME_BIND_HOST`. The installer manages it as follows; the deployed
CaticornQueen state is `0.0.0.0`.

- `-BindHost` omitted: the existing machine value is kept. Only a first install
  (no value set) defaults to `127.0.0.1`. A rerun therefore never changes the
  bind.
- `-BindHost <address>` supplied: the value is applied and the installer prints
  a warning showing the old and new value.
- A Tailscale address (`100.64.0.0/10` or `fd7a:115c:a1e0::/48`, for example
  `100.78.105.19`) is rejected, whether supplied or already stored. Tailscale
  Serve proxies the pairing paths to `127.0.0.1:8780` (see
  [Personal-client pairing](#personal-client-pairing-home-nw-17)), so a bind to
  the tailnet address alone breaks pairing.
- The local Prometheus job and the installer's `/metrics` readiness probe are
  derived separately from the bind: `0.0.0.0`, `::` and `127.0.0.1` all scrape
  `127.0.0.1:<Port>`; only another specific address is used as given (IPv6 is
  bracketed).

| `-BindHost` | Stored bind | Local scrape target |
| --- | --- | --- |
| omitted, nothing stored | `127.0.0.1` | `127.0.0.1:8780` |
| omitted, `0.0.0.0` stored | `0.0.0.0` (kept) | `127.0.0.1:8780` |
| omitted, `127.0.0.1` stored | `127.0.0.1` (kept) | `127.0.0.1:8780` |
| `0.0.0.0` | `0.0.0.0` | `127.0.0.1:8780` |
| `127.0.0.1` | `127.0.0.1` | `127.0.0.1:8780` |
| `192.168.0.4` | `192.168.0.4` | `192.168.0.4:8780` |
| a Tailscale address | rejected before any change | none |

### Remote scraping from the ops Alloy

[`../ops/hermes-home.alloy`](../ops/hermes-home.alloy) scrapes
`100.78.105.19:8780/metrics` with the admin token. That needs Home listening on
a non-loopback address and a Windows Firewall allow that is limited to the
tailnet. The supported setup is `0.0.0.0` plus a tailnet-scoped rule, not a bind
to the tailnet address:

```powershell
.\install.ps1 -WheelPath $wheel.FullName -BindHost 0.0.0.0 -AllowTailnetMetricsScrape
```

`-AllowTailnetMetricsScrape` is opt-in. It creates or converges exactly one
inbound rule and reads it back; a mismatch fails the install:

| Field | Value |
| --- | --- |
| Display name | `Hermes Home metrics from Tailscale` |
| Direction / action | Inbound / Allow |
| Protocol / local port | TCP / `-Port` (`8780`) |
| Remote address | `100.64.0.0/10` (Windows reads it back as `100.64.0.0/255.192.0.0`) |
| Profile | Private |

Without the switch the installer never touches the firewall, so it never opens
the port to the LAN or to any other address. With the switch and a loopback
bind it still creates the rule but warns that scraping needs `-BindHost 0.0.0.0`.
Once `HERMES_HOME_BIND_HOST` is `0.0.0.0`, later runs need no `-BindHost`; pass
`-AllowTailnetMetricsScrape` again only to converge the rule.

Verify on CaticornQueen (elevated PowerShell) after any install:

```powershell
[Environment]::GetEnvironmentVariable('HERMES_HOME_BIND_HOST', 'Machine')   # 0.0.0.0
Get-NetTCPConnection -State Listen -LocalPort 8780 | Select-Object LocalAddress, LocalPort
Get-NetFirewallRule -DisplayName 'Hermes Home metrics from Tailscale' |
  Format-List DisplayName, Enabled, Direction, Action, Profile
Get-NetFirewallRule -DisplayName 'Hermes Home metrics from Tailscale' |
  Get-NetFirewallAddressFilter | Select-Object RemoteAddress
Select-String -Path 'C:\Program Files\Prometheus\prometheus.yml' -Pattern "targets: \['127.0.0.1:8780'\]"
```

The listener must be `0.0.0.0:8780`, the rule must be Inbound/Allow/Private
with remote `100.64.0.0/255.192.0.0`, and the local Prometheus target must be
`127.0.0.1:8780`. From another tailnet device, `Test-NetConnection
100.78.105.19 -Port 8780` must succeed and `/metrics` must return `401` without
the token and `200` with it. From a device on the LAN but not the tailnet,
`curl --max-time 5 http://<LAN address of CaticornQueen>:8780/metrics` must time
out. On the ops host the Alloy `prometheus.scrape.hermes_home` component must be
`healthy` with an `up` target. `https://<home tailnet name>/pair` must still
answer `200` through Tailscale Serve.

Roll back by removing the rule and restoring the previous bind, then restarting
only the Home task:

```powershell
Remove-NetFirewallRule -DisplayName 'Hermes Home metrics from Tailscale'
[Environment]::SetEnvironmentVariable('HERMES_HOME_BIND_HOST', '127.0.0.1', 'Machine')
Stop-ScheduledTask -TaskName 'Hermes Home'
Start-ScheduledTask -TaskName 'Hermes Home'
```

(or `.\install.ps1 -WheelPath $wheel.FullName -BindHost 127.0.0.1`). Ops Alloy
will then report the target down; remove or disable the `hermes_home` scrape
there if loopback is the intended end state.

Package-only cutovers (stop the task, `uv pip install --force-reinstall` the
wheel, start the task) do not run the installer and so touch neither the bind,
the firewall rule, nor the Prometheus job.

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

The installer is idempotent: it preserves the existing admin token and the
existing `HERMES_HOME_BIND_HOST`, replaces only its marked Prometheus job,
validates the candidate configuration with `promtool`, saves a timestamped
backup, and restarts the Prometheus service.

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

[`hermes-home-export.alloy`](hermes-home-export.alloy) is an Alloy snippet
(`loki.source.file` to `loki.process` to `loki.write`) that would ship the
export to Loki. It is **not applied** and has not been run through Alloy. Its
Loki push URL is the placeholder environment variable
`HERMES_HOME_LOKI_PUSH_URL`; the real URL, authentication and tenant belong to
`ops` and are not recorded in this repository.

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
