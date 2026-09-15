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
cp deploy/ops/com.hermes.home-standard-pilot.plist ~/Library/LaunchAgents/
chmod 700 ~/.hermes/hermes-home-standard-pilot/run.sh
chmod 600 ~/.hermes/hermes-home-standard-pilot/standard-token
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hermes.home-standard-pilot.plist
launchctl kickstart -k gui/$(id -u)/com.hermes.home-standard-pilot
```

Confirm the process emits `gateway.ready` on a loopback handshake before
adding the tailnet route. Then add only the versioned Standard path to the
existing Tailscale Serve configuration:

```sh
/usr/local/bin/tailscale serve --bg --https=8443 \
  --set-path=/api/ws \
  http://127.0.0.1:9120/api/ws
```

The resulting Home setting is the tailnet WSS URL ending in `/api/ws`. Keep
the Standard token on the media server and copy the same value into the
ACL-protected CaticornQueen token file used by the Windows installer. This
process is the Sprint 1 pilot target; HOME-NW-05 still owns the eventual
dynamic Profile and claim authority.
