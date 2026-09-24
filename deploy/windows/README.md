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

## Personal-client pairing (HOME-NW-17)

TUI, iOS, macOS, and Android clients pair from the Home pairing page and then reach
Home over the tailnet. Publish only the pairing page and the device-facing
routes; admin routes (offers, request listing, approval, rotation,
configuration, metrics, diagnostics) stay loopback-only and are reached only
through the signed-in page. Each path repeats in the target because Tailscale
strips the `--set-path` prefix:

```powershell
foreach ($path in '/pair', '/api/v1/enrollment/requests', '/api/v1/client-claims',
                  '/api/v1/client-sessions', '/api/v1/profile-grants', '/api/v1/devices') {
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
