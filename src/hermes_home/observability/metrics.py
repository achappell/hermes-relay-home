"""Small dependency-free Prometheus metrics registry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from threading import RLock

_HISTOGRAM_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    float("inf"),
)
_METRIC_DEFINITIONS = {
    "hermes_home_configuration_publishes_total": (
        "counter",
        "Configuration publish attempts by outcome.",
    ),
    "hermes_home_configuration_revision": (
        "gauge",
        "Currently observed active configuration revision.",
    ),
    "hermes_home_diagnostics_collector_reachable": (
        "gauge",
        "Whether the configured diagnostics collector is reachable.",
    ),
    "hermes_home_diagnostics_events_dropped_total": (
        "counter",
        "Automatic diagnostic events dropped by the bounded local store.",
    ),
    "hermes_home_diagnostics_events_rejected_total": (
        "counter",
        "Automatic diagnostic events rejected by schema or storage policy.",
    ),
    "hermes_home_diagnostics_events_total": (
        "counter",
        "Automatic diagnostic events accepted by source and outcome.",
    ),
    "hermes_home_diagnostics_last_upload_timestamp_seconds": (
        "gauge",
        "Unix timestamp of the last successful diagnostics upload.",
    ),
    "hermes_home_diagnostics_queue_depth": (
        "gauge",
        "Number of safe diagnostic events awaiting upload.",
    ),
    "hermes_home_diagnostics_ring_entries_evicted_total": (
        "counter",
        "Private ring-buffer evidence entries evicted by capacity.",
    ),
    "hermes_home_diagnostics_ring_entries_expired_total": (
        "counter",
        "Private ring-buffer evidence entries expired by age.",
    ),
    "hermes_home_diagnostics_ring_entries_out_of_order_total": (
        "counter",
        "Private ring-buffer evidence entries dropped as stale.",
    ),
    "hermes_home_diagnostics_uploads_total": (
        "counter",
        "Safe diagnostic upload attempts by outcome.",
    ),
    "hermes_home_http_request_duration_seconds": (
        "histogram",
        "HTTP request duration in seconds.",
    ),
    "hermes_home_http_requests_total": (
        "counter",
        "HTTP requests handled by method, route, and status.",
    ),
    "hermes_home_wake_claims_total": (
        "counter",
        "Wake claims handled by outcome.",
    ),
    "hermes_home_wake_decisions_total": (
        "counter",
        "Final wake arbitration decisions by decision.",
    ),
}


@dataclass(slots=True)
class _HistogramState:
    count: int = 0
    total: float = 0.0
    buckets: dict[float, int] = field(
        default_factory=lambda: dict.fromkeys(_HISTOGRAM_BUCKETS, 0)
    )


class MetricsRegistry:
    """Thread-safe counters, gauges, and histograms rendered as Prometheus text."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[
            tuple[str, tuple[tuple[str, str], ...]], _HistogramState
        ] = {}

    def inc(
        self,
        name: str,
        *,
        labels: Mapping[str, str] | None = None,
        value: float = 1.0,
    ) -> None:
        self._require_type(name, "counter")
        amount = _finite_number(value)
        if amount < 0:
            raise ValueError("counter increments must be non-negative")
        key = (name, _normalise_labels(labels))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + amount

    def set(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        self._require_type(name, "gauge")
        gauge_value = _finite_number(value)
        key = (name, _normalise_labels(labels))
        with self._lock:
            self._gauges[key] = gauge_value

    def observe(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        self._require_type(name, "histogram")
        observation = _finite_number(value)
        if observation < 0:
            raise ValueError("histogram observations must be non-negative")
        key = (name, _normalise_labels(labels))
        with self._lock:
            state = self._histograms.setdefault(key, _HistogramState())
            state.count += 1
            state.total += observation
            for bucket in _HISTOGRAM_BUCKETS:
                if observation <= bucket:
                    state.buckets[bucket] += 1

    def render(self) -> str:
        """Return a deterministic Prometheus text exposition."""
        with self._lock:
            lines: list[str] = []
            for name in sorted(_METRIC_DEFINITIONS):
                metric_type, help_text = _METRIC_DEFINITIONS[name]
                lines.append(f"# HELP {name} {help_text}")
                lines.append(f"# TYPE {name} {metric_type}")
                if metric_type == "counter":
                    lines.extend(
                        _render_samples(
                            name,
                            (
                                (labels, value)
                                for (
                                    metric_name,
                                    labels,
                                ), value in self._counters.items()
                                if metric_name == name
                            ),
                        )
                    )
                elif metric_type == "gauge":
                    lines.extend(
                        _render_samples(
                            name,
                            (
                                (labels, value)
                                for (metric_name, labels), value in self._gauges.items()
                                if metric_name == name
                            ),
                        )
                    )
                else:
                    lines.extend(
                        _render_histograms(
                            name,
                            (
                                (labels, state)
                                for (
                                    metric_name,
                                    labels,
                                ), state in self._histograms.items()
                                if metric_name == name
                            ),
                        )
                    )
            return "\n".join(lines) + "\n"

    @staticmethod
    def _require_type(name: str, expected_type: str) -> None:
        definition = _METRIC_DEFINITIONS.get(name)
        if definition is None:
            raise KeyError(f"unknown metric {name!r}")
        if definition[0] != expected_type:
            raise TypeError(f"{name} is not a {expected_type}")


def _finite_number(value: float) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise TypeError("metric values must be numeric")
    number = float(value)
    if not isfinite(number):
        raise ValueError("metric values must be finite")
    return number


def _normalise_labels(
    labels: Mapping[str, str] | None,
) -> tuple[tuple[str, str], ...]:
    if labels is None:
        return ()
    return tuple(sorted((str(name), str(value)) for name, value in labels.items()))


def _render_samples(
    name: str,
    samples: object,
) -> list[str]:
    rendered = []
    for labels, value in sorted(samples):
        rendered.append(f"{name}{_format_labels(labels)} {_format_number(value)}")
    return rendered


def _render_histograms(
    name: str,
    histograms: object,
) -> list[str]:
    rendered = []
    for labels, state in sorted(histograms):
        for bucket in _HISTOGRAM_BUCKETS:
            bucket_labels = labels + (("le", _bucket_label(bucket)),)
            rendered.append(
                f"{name}_bucket{_format_labels(bucket_labels)} {state.buckets[bucket]}"
            )
        rendered.append(
            f"{name}_sum{_format_labels(labels)} {_format_number(state.total)}"
        )
        rendered.append(f"{name}_count{_format_labels(labels)} {state.count}")
    return rendered


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    values = ",".join(f'{name}="{_escape_label(value)}"' for name, value in labels)
    return "{" + values + "}"


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _bucket_label(bucket: float) -> str:
    if bucket == float("inf"):
        return "+Inf"
    return _format_number(bucket)


def _format_number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return format(value, ".15g")
