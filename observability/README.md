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

## Safe-event and client-report export

Set `HERMES_HOME_EXPORT_DIR` to enable the local export. Home then writes
append-only JSONL files that are `fsync`-ed before a batch is acknowledged:

- `safe-events.<UTC day>.<seq>.jsonl` holds `record_type` `safe_event` lines
  (the same fields as `DiagnosticEvent.to_dict()`) and one `batch_ledger` line
  per upload batch carrying its idempotency key and event IDs. Replaying a
  batch with a remembered key writes nothing again. Retention is 14 days.
- `client-reports.<UTC day>.<seq>.jsonl` holds one `client_report` line per newly
  accepted opted-in client report (`device_id`, `received_at` and the stored
  payload). A failed or blocked export never changes the client's response,
  rate limit or stored report. Retention is 7 days, matching the stored table.

Files roll by size (4 MiB) and by UTC day, at most 8 per stream, and a sweep
deletes a file only once every line it could hold is past retention. Files are
created owner-only; on Windows protect the directory with the same DACL as the
operational diagnostics directory. `HERMES_HOME_EXPORT_INTERVAL_SECONDS`
(default 30, 1 to 3600) sets the drain cadence. Drain batches are at most 256
events and 1 MiB, run on their own thread, and back off from 30 seconds to 15
minutes with jitter after a failure. Shutdown does one final drain bounded to
about two seconds.

With no `HERMES_HOME_EXPORT_DIR`, status reports `collector_configured: false`
and `collector_state: "not_configured"`, `hermes_home_diagnostics_collector_configured`
is `0`, `collector_reachable` is never true, and no upload attempt is counted.
With it set, `collector_state` is `reachable` or `unreachable` (events are queued
and the last attempt did not succeed) or `idle` (nothing queued). It describes the
last export attempt and means the local export store acknowledged the write, not
that a remote collector is up. `dropped_unuploaded_event_count` counts events
evicted from the bounded 4096-event queue before they were exported.

Added metrics (labels never include device, report, launch or correlation IDs):

- `hermes_home_diagnostics_collector_configured`,
  `hermes_home_diagnostics_export_last_attempt_timestamp_seconds`,
  `hermes_home_diagnostics_events_evicted_total{state}`
- `hermes_home_export_records_written_total{record_type}`,
  `hermes_home_export_write_failures_total{record_type}`,
  `hermes_home_export_records_dropped_total{record_type,reason}`,
  `hermes_home_export_files_removed_total{record_type,reason}`
- `hermes_home_client_reports_total{outcome,platform}` for accepted, duplicate,
  invalid, conflict, rate_limited, unauthorized and storage_error intake, and
  `hermes_home_client_reports_retained{platform}`, refreshed on each scrape.

The provisionable dashboards
[`hermes-home-diagnostics.json`](grafana/dashboards/hermes-home-diagnostics.json)
(Prometheus) and
[`hermes-home-diagnostics-logs.json`](grafana/dashboards/hermes-home-diagnostics-logs.json)
(Loki) cover these series and the exported lines. The logs dashboard needs the
Loki shipper in [`../deploy/windows/hermes-home-export.alloy`](../deploy/windows/hermes-home-export.alloy),
which is validated with Alloy v1.20.1; installing it is a separate host step
documented in [`../deploy/windows/README.md`](../deploy/windows/README.md).

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
