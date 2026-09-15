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

## Standard-backed pilot bridge

For Sprint 1 live-gate evidence, the Home route can use one dedicated Standard
gateway target and an operator-managed conversation-grant file. This is a
bounded deployment seam while HOME-NW-05 builds the durable Profile and claim
API; it is not a replacement for that story.

Create the Standard server token on the Standard host, create the paired Home
root secret on CaticornQueen, and pass the three pilot settings together:

```powershell
.\install.ps1 -WheelPath $wheel.FullName `
  -CredentialRootSecretFile C:\ProgramData\HermesHome\secrets\credential-root `
  -StandardGatewayUrl 'wss://media-server.<tailnet>/api/ws' `
  -StandardTokenFile C:\ProgramData\HermesHome\secrets\standard-token `
  -ConversationGrantsFile C:\ProgramData\HermesHome\secrets\conversation-grants.json
```

The Standard token file is read by the Home process and never placed in an
endpoint response. The grants file is server-side JSON with this shape:

```json
{"schema":1,"grants":[{"handle":"<opaque-handle>","device_id":"<home-device-id>","profile_id":"amanda","status":"active"}]}
```

Home verifies the paired Device credential, resolves the opaque handle, opens
Standard with `source=home` and the selected Profile, and records the durable
Standard session ID back into the same file. Keep the file ACL restricted to
SYSTEM and local administrators. The installer creates an empty registry when
the path does not exist, but it does not invent device IDs, handles, Profiles,
or credentials.

The three settings are all-or-nothing. Removing them on a later install clears
the machine environment and returns the route to the safe unavailable bridge.
The route remains tailnet-only; a connected physical Android device and an
approved live Profile are still required for the final audio/reconnect proof.

The installer is idempotent: it preserves the existing admin token, replaces only its marked
Prometheus job, validates the candidate configuration with `promtool`, saves a
timestamped backup, and restarts the Prometheus service.

The resulting process is managed by the `Hermes Home` scheduled task. Its data,
logs, virtual environment, and secret live beneath
`C:\ProgramData\HermesHome`. The installer waits for both `/metrics` and an
`up{job="hermes-home"}` Prometheus result before returning success.
