# Hermes Home observability

The Home service exposes an admin-authenticated Prometheus text endpoint at
`GET /metrics`. It contains low-cardinality operational metrics only: HTTP
route/status, request duration, configuration revision and publish outcomes,
and wake-claim/arbitration outcomes. Device IDs, claim IDs, credentials,
transcripts, prompts, and audio are deliberately absent.

## Household diagnostics

NW-06 adds a separate, content-safe review boundary alongside the metrics
endpoint:

- `GET /api/v1/diagnostics/status` is available to the admin or an
  authenticated Home device. It reports whether diagnostics are enabled, the
  queued safe-event count, collector reachability, the last successful upload,
  bounded-loss counters, and the retention policy.
- `GET /api/v1/diagnostics/timeline/{correlation_id}` is admin-only. It returns
  the ordered, versioned lifecycle events for one opaque correlation ID.

Automatic events contain typed lifecycle facts, approved route labels, opaque
fingerprints, durations, byte counts, and upload state. They reject prompts,
transcripts, raw audio, credentials, keys, Sensitive Entry values, private
notifications, and reversible household identifiers. The local SQLite review
store retains detailed events for 14 days and bounds the queue; safe metrics
retain for 30 days. A collector is an injected port, so a collector outage
leaves the local queue and status honest without retrying or replaying a live
Hermes turn.

Incident capture is a separate explicit path. Home fixes the endpoint/current
task scope, preview-before-approval rule, seven-day bundle deadline, and
preserve/delete audit transitions through `IncidentCaptureService`. Endpoint
adapters supply any private ring-buffer evidence, while sealing and bundle
upload remain injected ports until the final encrypted store and trusted
review roles are selected. A failed turn never uploads incident evidence by
itself.

## Scrape

Run Prometheus where it can reach the Home service and configure the admin
credential as a secret file. The repository includes a starting point at
[`prometheus/prometheus.yml.example`](prometheus/prometheus.yml.example).

The development server binds to loopback by default. A Prometheus process in a
container needs an explicitly reachable Home-service address; changing the
Home bind address is a deployment decision inside the private household trust
boundary.

## Grafana

The provisionable dashboards are:

- [`grafana/dashboards/hermes-home-overview.json`](grafana/dashboards/hermes-home-overview.json)
  — request health, latency, configuration revision, wake outcomes, and publish
  rate;
- [`grafana/dashboards/hermes-home-debug.json`](grafana/dashboards/hermes-home-debug.json)
  — route-level errors, slow requests, failed claims, and failed publishes.

Copy [`grafana/provisioning/dashboards/home-service.yaml`](grafana/provisioning/dashboards/home-service.yaml)
into Grafana's provisioning directory, and mount the dashboard directory at
the path named by its `options.path`. The optional
[`grafana/provisioning/datasources/prometheus.yaml`](grafana/provisioning/datasources/prometheus.yaml)
provisions a local Prometheus data source named `Prometheus`; change its URL for
another deployment. You can also select another Prometheus-compatible source
when importing the JSON manually.

The dashboards intentionally contain no alert notification channels. Alert
ownership and household-hours thresholds should be chosen after the service
has real traffic rather than embalming guesses in a JSON file.

For the native Windows deployment used by CaticornQueen, see
[`../deploy/windows/README.md`](../deploy/windows/README.md). It installs the
runtime, protects the admin token, configures the authenticated Prometheus
scrape, and verifies the target before returning.

For the cross-host scrape that feeds the household Grafana instance, see
[`../deploy/ops/README.md`](../deploy/ops/README.md). It adds the authenticated
Home target to Alloy, which remote-writes into the Prometheus used by Grafana.
