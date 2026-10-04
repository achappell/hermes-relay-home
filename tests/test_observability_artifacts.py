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
