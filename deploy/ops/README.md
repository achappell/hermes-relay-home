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
