import json
from pathlib import Path

OBSERVABILITY = Path(__file__).parents[1] / "observability"


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
