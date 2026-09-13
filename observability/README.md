# Hermes Home observability

The Home service exposes an admin-authenticated Prometheus text endpoint at
`GET /metrics`. It contains low-cardinality operational metrics only: HTTP
route/status, request duration, configuration revision and publish outcomes,
and wake-claim/arbitration outcomes. Device IDs, claim IDs, credentials,
transcripts, prompts, and audio are deliberately absent.

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
