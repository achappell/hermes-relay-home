import json
from pathlib import Path

OBSERVABILITY = Path(__file__).parents[1] / "observability"


def test_diagnostics_review_boundary_is_documented_without_content_logging() -> None:
    readme = (OBSERVABILITY / "README.md").read_text()

    assert "GET /api/v1/diagnostics/status" in readme
    assert "GET /api/v1/diagnostics/timeline/{correlation_id}" in readme
    assert "14 days" in readme
    assert "30 days" in readme
    assert "raw audio" in readme
    assert "preview-before-approval" in readme


def test_grafana_dashboards_are_valid_and_reference_the_home_metrics() -> None:
    for name, uid in (
        ("hermes-home-overview.json", "hermes-home-overview"),
        ("hermes-home-debug.json", "hermes-home-debug"),
    ):
        dashboard = json.loads(
            (OBSERVABILITY / "grafana" / "dashboards" / name).read_text()
        )

        assert dashboard["uid"] == uid
        assert dashboard["templating"]["list"][0]["name"] == "DS_PROMETHEUS"
        assert dashboard["panels"]
        expressions = [
            target["expr"]
            for panel in dashboard["panels"]
            for target in panel.get("targets", [])
        ]
        assert expressions
        assert all(
            "hermes_home_" in expression or expression.startswith("up{")
            for expression in expressions
        )


def test_grafana_provisioning_points_at_versioned_dashboard_artifacts() -> None:
    dashboard_provider = (
        OBSERVABILITY / "grafana" / "provisioning" / "dashboards" / "home-service.yaml"
    ).read_text()
    datasource_provider = (
        OBSERVABILITY / "grafana" / "provisioning" / "datasources" / "prometheus.yaml"
    ).read_text()

    assert "type: file" in dashboard_provider
    assert "path: /etc/grafana/dashboards/hermes-home" in dashboard_provider
    assert "name: Prometheus" in datasource_provider
    assert "type: prometheus" in datasource_provider


def test_ops_alloy_artifact_scrapes_home_with_a_bearer_secret() -> None:
    artifact = (
        Path(__file__).parents[1] / "deploy" / "ops" / "hermes-home.alloy"
    ).read_text()

    assert 'prometheus.scrape "hermes_home"' in artifact
    assert 'job_name        = "hermes-home"' in artifact
    assert "prometheus.remote_write.default.receiver" in artifact
    assert 'type             = "Bearer"' in artifact
    assert 'credentials_file = "/etc/alloy/secrets/hermes-home-admin-token"' in artifact


def test_diagnostics_dashboards_are_valid_and_use_only_defined_metrics() -> None:
    from hermes_home.observability.metrics import _METRIC_DEFINITIONS

    for name, uid, datasource in (
        ("hermes-home-diagnostics.json", "hermes-home-diagnostics", "DS_PROMETHEUS"),
        (
            "hermes-home-diagnostics-logs.json",
            "hermes-home-diagnostics-logs",
            "DS_LOKI",
        ),
    ):
        dashboard = json.loads(
            (OBSERVABILITY / "grafana" / "dashboards" / name).read_text()
        )
        assert dashboard["uid"] == uid
        assert dashboard["templating"]["list"][0]["name"] == datasource
        assert dashboard["panels"]

    metrics_dashboard = json.loads(
        (
            OBSERVABILITY / "grafana" / "dashboards" / "hermes-home-diagnostics.json"
        ).read_text()
    )
    expressions = " ".join(
        target["expr"]
        for panel in metrics_dashboard["panels"]
        for target in panel.get("targets", [])
    )
    for metric in (
        "hermes_home_diagnostics_queue_depth",
        "hermes_home_diagnostics_collector_configured",
        "hermes_home_diagnostics_collector_reachable",
        "hermes_home_diagnostics_last_upload_timestamp_seconds",
        "hermes_home_diagnostics_uploads_total",
        "hermes_home_diagnostics_events_evicted_total",
        "hermes_home_client_reports_total",
        "hermes_home_client_reports_retained",
        "hermes_home_export_write_failures_total",
    ):
        assert metric in expressions
        assert metric in _METRIC_DEFINITIONS
    assert "device" not in expressions
    assert "report_id" not in expressions


def test_logs_dashboard_queries_only_the_low_cardinality_export_labels() -> None:
    dashboard = json.loads(
        (
            OBSERVABILITY
            / "grafana"
            / "dashboards"
            / "hermes-home-diagnostics-logs.json"
        ).read_text()
    )
    expressions = [
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    ]

    assert expressions
    assert all('job="hermes-home-export"' in expression for expression in expressions)
    assert any('record_type="client_report"' in e for e in expressions)
    assert any('record_type="safe_event"' in e for e in expressions)


def test_export_shipper_snippet_is_validated_and_documented() -> None:
    root = Path(__file__).parents[1] / "deploy" / "windows"
    snippet = (root / "hermes-home-export.alloy").read_text()
    readme = (root / "README.md").read_text()

    assert "NOT APPLIED" not in snippet
    assert "Alloy v1.20.1" in snippet
    assert 'loki.source.file "hermes_home_export"' in snippet
    assert 'loki.write "household"' in snippet
    # Bare `env(...)` is deprecated in Alloy v1.20.1 and fails `alloy validate`.
    assert 'url = sys.env("HERMES_HOME_LOKI_PUSH_URL")' in snippet
    assert ' env("' not in snippet
    assert "safe-events.*.jsonl" in snippet
    assert "client-reports.*.jsonl" in snippet
    # Only the record type is promoted to a label; identifiers stay in the body.
    assert "stage.labels" in snippet
    for forbidden in ("device_id", "report_id", "launch_id", "correlation_id"):
        assert forbidden not in snippet.split("stage.labels", 1)[1]
    assert "password" not in snippet.lower()
    assert "HERMES_HOME_EXPORT_DIR" in readme
    assert "hermes-home-export.alloy" in readme
    assert "sys.env" in readme
    assert "NT SERVICE\\Alloy" in readme
    assert "never the default LocalSystem" in readme
    assert "HKLM:\\Software\\GrafanaLabs\\Alloy" in readme


def test_observability_readme_documents_export_retention_and_not_configured() -> None:
    readme = (OBSERVABILITY / "README.md").read_text()

    assert "HERMES_HOME_EXPORT_DIR" in readme
    assert "not_configured" in readme
    assert "7 days" in readme
    assert "hermes_home_client_reports_total" in readme
