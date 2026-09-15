# Windows deployment

The household Prometheus instance runs on CaticornQueen as a native Windows
service. This bundle installs the Home wheel into a Python 3.14 virtual
environment, runs it as a SYSTEM scheduled task, stores the admin credential in
an ACL-protected file, and adds an authenticated `hermes-home` scrape job to
Prometheus.

The installer defaults to loopback on port `8780` because CaticornQueen already
uses port `8765` for the Qwen TTS service. Prometheus scrapes the Home process
locally; LAN binding remains a separate, deliberate trust-boundary decision.

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

To enable paired mode, create the operator-owned 64-character hexadecimal
root-secret file first and pass it explicitly:

```powershell
.\install.ps1 -WheelPath $wheel.FullName -CredentialRootSecretFile C:\ProgramData\HermesHome\secrets\credential-root
```

The installer never creates that root secret. Without it, and without the
legacy `-DeviceCredentialsFile` option, endpoint authentication remains
disabled. The two credential modes cannot be supplied together.

The installer is idempotent: it preserves the existing admin token, replaces only its marked
Prometheus job, validates the candidate configuration with `promtool`, saves a
timestamped backup, and restarts the Prometheus service.

The resulting process is managed by the `Hermes Home` scheduled task. Its data,
logs, virtual environment, and secret live beneath
`C:\ProgramData\HermesHome`. The installer waits for both `/metrics` and an
`up{job="hermes-home"}` Prometheus result before returning success.
