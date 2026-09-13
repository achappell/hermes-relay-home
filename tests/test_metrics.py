from hermes_home.observability.metrics import MetricsRegistry


def test_metrics_registry_renders_counters_gauges_and_histograms() -> None:
    metrics = MetricsRegistry()

    metrics.inc(
        "hermes_home_http_requests_total",
        labels={"method": "GET", "route": "configuration", "status": "200"},
    )
    metrics.set("hermes_home_configuration_revision", 12)
    metrics.observe(
        "hermes_home_http_request_duration_seconds",
        0.125,
        labels={"method": "GET", "route": "configuration"},
    )

    rendered = metrics.render()

    assert (
        'hermes_home_http_requests_total{method="GET",route="configuration",status="200"} 1'
        in rendered
    )
    assert "hermes_home_configuration_revision 12" in rendered
    assert (
        'hermes_home_http_request_duration_seconds_bucket{method="GET",route="configuration",le="0.25"} 1'
        in rendered
    )
    assert (
        'hermes_home_http_request_duration_seconds_count{method="GET",route="configuration"} 1'
        in rendered
    )


def test_metrics_registry_escapes_label_values() -> None:
    metrics = MetricsRegistry()

    metrics.inc(
        "hermes_home_http_requests_total",
        labels={"method": "GET", "route": 'a\\b"c\nd', "status": "400"},
    )

    assert 'route="a\\\\b\\"c\\nd"' in metrics.render()
