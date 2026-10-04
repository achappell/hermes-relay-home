# Ops observability deployment

The Grafana instance at `grafana.chappell-home.dev` reads the Prometheus
container on the `ops` host. That Prometheus receives metrics through the
existing Alloy remote-write pipeline, so Home must be added as an authenticated
Alloy scrape rather than as a direct Prometheus scrape.

Append [`hermes-home.alloy`](hermes-home.alloy) to the ops Alloy configuration
and provision the same Home admin token into the host-only file
`/srv/ops/alloy/secrets/hermes-home-admin-token`. Protect it with mode `0600`
and do not commit or print it. The Alloy container must mount that file
read-only:

```yaml
      - ./secrets/hermes-home-admin-token:/etc/alloy/secrets/hermes-home-admin-token:ro
```

Validate the compose configuration and reload Alloy after the change. Confirm
that the Alloy health page is ready, the scrape has no authentication error,
and Prometheus reports `up{job="hermes-home",host="caticornqueen"} == 1`.

The Windows installer must be run with `-BindHost 100.78.105.19` for this
cross-host scrape. That Tailscale-only binding keeps the service off the LAN
while making it reachable from the ops collector.

## Standard-backed Home pilot

The Sprint 1 live gate uses a separate, Profile-scoped Hermes Standard process
on the media server. The checked-in wrapper reads its server-to-server token
from `~/.hermes/hermes-home-standard-pilot/standard-token`; the token is never
placed in the launchd plist or a command line. The default Profile is `amanda`
and the loopback listener is port `9120`.

Install the wrapper and plist as the Hermes service user, then create the token
file with mode `0600` and load the LaunchAgent:

```sh
mkdir -p ~/.hermes/hermes-home-standard-pilot ~/Library/LaunchAgents
chmod 700 ~/.hermes/hermes-home-standard-pilot
cp deploy/ops/hermes-standard-home-pilot.sh ~/.hermes/hermes-home-standard-pilot/run.sh
cp deploy/ops/hermes-standard-home-pilot-proxy.py ~/.hermes/hermes-home-standard-pilot/proxy.py
cp src/hermes_home_diagnostics.py ~/.hermes/hermes-home-standard-pilot/hermes_home_diagnostics.py
cp deploy/ops/hermes-standard-home-pilot-proxy.sh ~/.hermes/hermes-home-standard-pilot/proxy.sh
cp deploy/ops/com.hermes.home-standard-pilot.plist ~/Library/LaunchAgents/
cp deploy/ops/com.hermes.home-standard-pilot-proxy.plist ~/Library/LaunchAgents/
chmod 700 ~/.hermes/hermes-home-standard-pilot/run.sh
chmod 700 ~/.hermes/hermes-home-standard-pilot/proxy.sh
chmod 700 ~/.hermes/hermes-home-standard-pilot/proxy.py
chmod 700 ~/.hermes/hermes-home-standard-pilot/hermes_home_diagnostics.py
chmod 600 ~/.hermes/hermes-home-standard-pilot/standard-token
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hermes.home-standard-pilot.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hermes.home-standard-pilot-proxy.plist
launchctl kickstart -k gui/$(id -u)/com.hermes.home-standard-pilot
launchctl kickstart -k gui/$(id -u)/com.hermes.home-standard-pilot-proxy
```

## Proxy diagnostic records

`hermes_home_diagnostics.py` must be copied from the same checkout as
`proxy.py`; the standard-library helper supports CPython 3.9+. Continue using
the existing Hermes Agent Python environment (CPython 3.9+ with
`websockets.sync` available); this adds no agent venv or dependency
installation.
The wrapper sets `HERMES_HOME_PROXY_LOG_DIR` to
`~/.hermes/hermes-home-standard-pilot/logs` unless that variable is already
configured. The sink creates an owner-only directory and files; keep the
service account as the writer and give only authorized operators read access.

The active `proxy.jsonl` is bounded to 10 MiB with four rotated backups and
14-day expiry (50 MiB maximum for this process). Records are at most 2 KiB;
the nonblocking queue holds 1,024 records and drops newest when full. Loss
counters are process-local status; the sink emits best-effort JSONL loss
snapshots at most once per minute after losses accumulate. A total sink failure
or crash may leave counters unavailable, and sink failures do not block the
relay. A crash can leave only the final line incomplete. Retrieve JSONL locally
as the service owner or an authorized operator, for example:

```sh
jq -c . ~/.hermes/hermes-home-standard-pilot/logs/proxy.jsonl
```

These files are not served or uploaded. The wrapper no longer appends a second
stdout/stderr copy to `proxy.log`; any older `proxy.log` is not used by the
new runner.

Confirm the process emits `gateway.ready` on a loopback handshake before
adding the tailnet route. Then add only the versioned Standard path to the
existing Tailscale Serve configuration:

```sh
/usr/local/bin/tailscale serve --bg --https=8443 \
  --set-path=/api/ws \
  http://127.0.0.1:9121/api/ws
/usr/local/bin/tailscale serve --bg --https=8443 \
  --set-path=/api/audio/speak-stream \
  http://127.0.0.1:9121/api/audio/speak-stream
```

The relay remains loopback-only. It checks the same Standard token as the
Home client, then opens a fresh loopback WebSocket to Standard so the public
Tailscale `Host` header cannot trip Hermes's loopback rebinding guard. The
second route is required for Home's separate response-audio socket. The
resulting Home setting is the tailnet WSS URL ending in `/api/ws`. Keep the
Standard token on the media server and copy the same value into the
ACL-protected CaticornQueen token file used by the Windows installer. This
process is the Sprint 1 pilot target; HOME-NW-05 still owns the eventual
dynamic Profile and claim authority.
