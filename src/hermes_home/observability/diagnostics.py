"""Content-safe diagnostics records and the local review boundary."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from math import isfinite
from threading import RLock
from typing import Protocol

from hermes_home.observability.metrics import MetricsRegistry

DIAGNOSTICS_SCHEMA = 1
METRIC_RETENTION_SECONDS = 30 * 24 * 60 * 60
EVENT_RETENTION_SECONDS = 14 * 24 * 60 * 60
INCIDENT_RETENTION_SECONDS = 7 * 24 * 60 * 60
DEFAULT_MAX_EVENTS = 4096
MAX_EVENT_ID_LENGTH = 128

_SOURCES = frozenset({"endpoint", "home", "hermes"})
_PHASES = frozenset(
    {
        "route",
        "authorization",
        "session",
        "turn",
        "audio",
        "health",
        "telemetry",
        "incident",
        "request",
    }
)
_OUTCOMES = frozenset(
    {
        "started",
        "accepted",
        "completed",
        "failed",
        "interrupted",
        "unavailable",
        "rejected",
        "queued",
        "uploaded",
        "dropped",
        "cancelled",
    }
)
_ROUTE_CLASSES = frozenset({"home", "tailscale", "public"})
_HEALTH_VALUES = frozenset({"healthy", "degraded", "unavailable", "unknown"})
_RETENTION_CLASSES = frozenset({"metrics", "events", "incident"})
_UPLOAD_OUTCOMES = frozenset({"success", "failed", "unavailable"})
_RETENTION_SECONDS = {
    "metrics": METRIC_RETENTION_SECONDS,
    "events": EVENT_RETENTION_SECONDS,
    "incident": INCIDENT_RETENTION_SECONDS,
}
_FAILURE_CODES = frozenset(
    {
        "audio_fallback",
        "audio_unavailable",
        "authorization_unavailable",
        "capability_unavailable",
        "claim_denied",
        "conflict",
        "conversation_mismatch",
        "encryption_failed",
        "encryption_unavailable",
        "expired_or_consumed",
        "forbidden",
        "hermes_unavailable",
        "invalid_request",
        "not_found",
        "protocol_error",
        "reconnect_required",
        "request_rejected",
        "service_unavailable",
        "stale_conversation",
        "transport_timeout",
        "transport_unavailable",
        "unauthorized",
        "upload_failed",
        "upload_unavailable",
    }
)
_SAFE_ROUTE_IDS = frozenset(
    {
        "bridge",
        "configuration",
        "diagnostics_status",
        "diagnostics_timeline",
        "home",
        "local",
        "metrics",
        "other",
        "wake_claims",
    }
)
_SAFE_FIELDS = frozenset(
    {
        "schema",
        "event_id",
        "correlation_id",
        "source",
        "phase",
        "outcome",
        "occurred_at",
        "duration_ms",
        "failure_code",
        "route_class",
        "route_id",
        "health",
        "endpoint_fingerprint",
        "session_fingerprint",
        "turn_fingerprint",
        "service_version",
        "byte_count",
        "segment_count",
        "queue_depth",
        "upload_outcome",
        "retention_class",
        "retention_deadline",
    }
)


class DiagnosticValidationError(ValueError):
    """Raised when an automatic diagnostic record is not safe to retain."""


class DiagnosticStoreError(RuntimeError):
    """Raised when the local diagnostics review store cannot answer safely."""


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    """A typed automatic event with no content-bearing payload slot."""

    schema: int
    event_id: str
    correlation_id: str
    source: str
    phase: str
    outcome: str
    occurred_at: float
    duration_ms: int | None = None
    failure_code: str | None = None
    route_class: str | None = None
    route_id: str | None = None
    health: str | None = None
    endpoint_fingerprint: str | None = None
    session_fingerprint: str | None = None
    turn_fingerprint: str | None = None
    service_version: str | None = None
    byte_count: int | None = None
    segment_count: int | None = None
    queue_depth: int | None = None
    upload_outcome: str | None = None
    retention_class: str = "events"
    retention_deadline: float | None = None

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != DIAGNOSTICS_SCHEMA:
            raise DiagnosticValidationError("unsupported diagnostic schema")
        _require_token(self.event_id, "event_id", max_length=MAX_EVENT_ID_LENGTH)
        _require_token(self.correlation_id, "correlation_id")
        if self.source not in _SOURCES:
            raise DiagnosticValidationError("source is not safe")
        if self.phase not in _PHASES:
            raise DiagnosticValidationError("phase is not safe")
        if self.outcome not in _OUTCOMES:
            raise DiagnosticValidationError("outcome is not safe")
        _require_timestamp(self.occurred_at, "occurred_at")
        if self.duration_ms is not None:
            _require_count(self.duration_ms, "duration_ms")
        if self.failure_code is not None and self.failure_code not in _FAILURE_CODES:
            raise DiagnosticValidationError("failure code is not safe")
        if self.route_class is not None and self.route_class not in _ROUTE_CLASSES:
            raise DiagnosticValidationError("route class is not safe")
        if self.route_id is not None:
            _require_route_id(self.route_id)
        for value, name in (
            (self.endpoint_fingerprint, "endpoint_fingerprint"),
            (self.session_fingerprint, "session_fingerprint"),
            (self.turn_fingerprint, "turn_fingerprint"),
        ):
            if value is not None:
                _require_fingerprint(value, name)
        if self.service_version is not None:
            _require_service_version(self.service_version)
        if self.health is not None and self.health not in _HEALTH_VALUES:
            raise DiagnosticValidationError("health value is not safe")
        for value, name in (
            (self.byte_count, "byte_count"),
            (self.segment_count, "segment_count"),
            (self.queue_depth, "queue_depth"),
        ):
            if value is not None:
                _require_count(value, name)
        if (
            self.upload_outcome is not None
            and self.upload_outcome not in _UPLOAD_OUTCOMES
        ):
            raise DiagnosticValidationError("upload outcome is not safe")
        if self.retention_class not in _RETENTION_CLASSES:
            raise DiagnosticValidationError("retention class is not safe")
        if self.retention_deadline is not None:
            _require_timestamp(self.retention_deadline, "retention_deadline")

    @classmethod
    def create(
        cls,
        *,
        correlation_id: str,
        source: str,
        phase: str,
        outcome: str,
        occurred_at: float,
        event_id: str | None = None,
        **fields: object,
    ) -> DiagnosticEvent:
        """Create an event while keeping the safe field list explicit."""
        payload: dict[str, object] = {
            "schema": DIAGNOSTICS_SCHEMA,
            "event_id": event_id or f"evt-{uuid.uuid4().hex}",
            "correlation_id": correlation_id,
            "source": source,
            "phase": phase,
            "outcome": outcome,
            "occurred_at": occurred_at,
            **fields,
        }
        return cls.from_mapping(payload)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> DiagnosticEvent:
        """Validate an event mapping and reject unknown/content-bearing fields."""
        if not isinstance(payload, Mapping):
            raise DiagnosticValidationError("diagnostic event must be an object")
        unknown = set(payload) - _SAFE_FIELDS
        if unknown:
            names = ", ".join(sorted(str(name) for name in unknown))
            raise DiagnosticValidationError(f"unsupported diagnostic field: {names}")
        required = {
            "schema",
            "event_id",
            "correlation_id",
            "source",
            "phase",
            "outcome",
            "occurred_at",
        }
        if not required.issubset(payload):
            raise DiagnosticValidationError(
                "diagnostic event is missing required fields"
            )
        try:
            return cls(**{key: payload[key] for key in payload})  # type: ignore[arg-type]
        except TypeError as error:
            raise DiagnosticValidationError(
                "diagnostic event has invalid fields"
            ) from error

    def to_dict(self) -> dict[str, object]:
        """Return only the explicitly approved, non-null event fields."""
        values = {
            "schema": self.schema,
            "event_id": self.event_id,
            "correlation_id": self.correlation_id,
            "source": self.source,
            "phase": self.phase,
            "outcome": self.outcome,
            "occurred_at": self.occurred_at,
            "duration_ms": self.duration_ms,
            "failure_code": self.failure_code,
            "route_class": self.route_class,
            "route_id": self.route_id,
            "health": self.health,
            "endpoint_fingerprint": self.endpoint_fingerprint,
            "session_fingerprint": self.session_fingerprint,
            "turn_fingerprint": self.turn_fingerprint,
            "service_version": self.service_version,
            "byte_count": self.byte_count,
            "segment_count": self.segment_count,
            "queue_depth": self.queue_depth,
            "upload_outcome": self.upload_outcome,
            "retention_class": self.retention_class,
            "retention_deadline": self.retention_deadline,
        }
        return {key: value for key, value in values.items() if value is not None}


@dataclass(frozen=True, slots=True)
class DiagnosticsStatus:
    """Safe operational state exposed to an authenticated reviewer or endpoint."""

    enabled: bool
    last_successful_upload_at: float | None
    queued_event_count: int
    collector_reachable: bool
    dropped_event_count: int
    rejected_event_count: int
    ring_evicted_entry_count: int = 0
    ring_expired_entry_count: int = 0
    ring_out_of_order_drop_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": DIAGNOSTICS_SCHEMA,
            "enabled": self.enabled,
            "last_successful_upload_at": self.last_successful_upload_at,
            "queued_event_count": self.queued_event_count,
            "collector_reachable": self.collector_reachable,
            "dropped_event_count": self.dropped_event_count,
            "rejected_event_count": self.rejected_event_count,
            "ring_evicted_entry_count": self.ring_evicted_entry_count,
            "ring_expired_entry_count": self.ring_expired_entry_count,
            "ring_out_of_order_drop_count": self.ring_out_of_order_drop_count,
            "retention_seconds": {
                "metrics": METRIC_RETENTION_SECONDS,
                "events": EVENT_RETENTION_SECONDS,
                "incident": INCIDENT_RETENTION_SECONDS,
            },
        }


@dataclass(frozen=True, slots=True)
class UploadResult:
    """The honest outcome of one explicit safe-event upload attempt."""

    uploaded_count: int
    collector_reachable: bool


@dataclass(frozen=True, slots=True)
class UploadAcknowledgement:
    """Remote acknowledgement that one stable event batch was accepted."""

    idempotency_key: str
    event_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_token(self.idempotency_key, "idempotency_key")
        if not self.event_ids or any(
            type(event_id) is not str or not event_id for event_id in self.event_ids
        ):
            raise DiagnosticValidationError("upload acknowledgement IDs are invalid")
        if len(set(self.event_ids)) != len(self.event_ids):
            raise DiagnosticValidationError("upload acknowledgement IDs are duplicated")


class EventCollector(Protocol):
    """Port for a remote structured-event collector."""

    def upload(
        self,
        events: Sequence[DiagnosticEvent],
        *,
        idempotency_key: str,
    ) -> UploadAcknowledgement: ...


class DiagnosticsStore(Protocol):
    """Storage port for bounded safe events and upload metadata."""

    def append(self, event: DiagnosticEvent, *, max_events: int) -> bool: ...

    def events(
        self,
        *,
        correlation_id: str,
        before: float,
    ) -> tuple[DiagnosticEvent, ...]: ...

    def pending_events(
        self, *, limit: int, before: float
    ) -> tuple[DiagnosticEvent, ...]: ...

    def mark_uploaded(
        self, event_ids: Sequence[str], *, uploaded_at: float
    ) -> None: ...

    def purge_expired(self, *, before: float) -> int: ...

    def status(self) -> DiagnosticsStatus: ...

    def record_rejection(self) -> None: ...

    def mark_collector_unreachable(self) -> None: ...


class IncidentCaptureStore(Protocol):
    """Durable metadata and audit port for explicit incident captures."""

    def captures(self) -> tuple[CaptureRecord, ...]: ...

    def save_capture(self, capture: CaptureRecord) -> None: ...

    def append_audit(self, record: CaptureAuditRecord) -> None: ...

    def audit_log(self) -> tuple[CaptureAuditRecord, ...]: ...


class CaptureExpiryScheduler(Protocol):
    """Runtime-owned scheduler used to reap untouched capture state."""

    def register(
        self,
        callback: Callable[[], int],
        *,
        interval_seconds: float,
    ) -> object: ...

    def unregister(self, handle: object) -> None: ...


class InMemoryDiagnosticsStore:
    """Bounded store used by domain tests and dependency-injected adapters."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._events: deque[DiagnosticEvent] = deque()
        self._uploaded: set[str] = set()
        self._dropped_event_count = 0
        self._rejected_event_count = 0
        self._last_successful_upload_at: float | None = None
        self._collector_reachable = False

    def append(self, event: DiagnosticEvent, *, max_events: int) -> bool:
        if type(max_events) is not int or max_events < 1:
            raise ValueError("diagnostic event bound must be positive")
        with self._lock:
            if any(existing.event_id == event.event_id for existing in self._events):
                raise DiagnosticStoreError("duplicate diagnostic event ID")
            dropped = False
            while len(self._events) >= max_events:
                removed = self._events.popleft()
                self._uploaded.discard(removed.event_id)
                self._dropped_event_count += 1
                dropped = True
            self._events.append(event)
            return dropped

    def events(
        self,
        *,
        correlation_id: str,
        before: float,
    ) -> tuple[DiagnosticEvent, ...]:
        with self._lock:
            self._purge_expired(before)
            return tuple(
                sorted(
                    (
                        event
                        for event in self._events
                        if event.correlation_id == correlation_id
                    ),
                    key=lambda event: event.occurred_at,
                )
            )

    def pending_events(
        self, *, limit: int, before: float
    ) -> tuple[DiagnosticEvent, ...]:
        with self._lock:
            self._purge_expired(before)
            return tuple(
                event for event in self._events if event.event_id not in self._uploaded
            )[:limit]

    def mark_uploaded(self, event_ids: Sequence[str], *, uploaded_at: float) -> None:
        with self._lock:
            known = {event.event_id for event in self._events}
            self._uploaded.update(
                event_id for event_id in event_ids if event_id in known
            )
            self._last_successful_upload_at = uploaded_at
            self._collector_reachable = True

    def mark_collector_unreachable(self) -> None:
        with self._lock:
            self._collector_reachable = False

    def purge_expired(self, *, before: float) -> int:
        with self._lock:
            return self._purge_expired(before)

    def status(self) -> DiagnosticsStatus:
        with self._lock:
            queued = sum(event.event_id not in self._uploaded for event in self._events)
            return DiagnosticsStatus(
                enabled=True,
                last_successful_upload_at=self._last_successful_upload_at,
                queued_event_count=queued,
                collector_reachable=self._collector_reachable,
                dropped_event_count=self._dropped_event_count,
                rejected_event_count=self._rejected_event_count,
            )

    def record_rejection(self) -> None:
        with self._lock:
            self._rejected_event_count += 1

    def _purge_expired(self, before: float) -> int:
        removed = 0
        retained: deque[DiagnosticEvent] = deque()
        for event in self._events:
            if _event_expired(event, before=before):
                self._uploaded.discard(event.event_id)
                removed += 1
            else:
                retained.append(event)
        self._events = retained
        return removed


class DiagnosticsRecorder:
    """Record safe events without allowing diagnostics to control live work."""

    def __init__(
        self,
        *,
        store: DiagnosticsStore,
        metrics: MetricsRegistry | None = None,
        collector: EventCollector | None = None,
        clock: Callable[[], float] = time.time,
        max_events: int = DEFAULT_MAX_EVENTS,
        event_retention_seconds: int = EVENT_RETENTION_SECONDS,
        ring_buffer: LocalRingBuffer | None = None,
    ) -> None:
        if type(max_events) is not int or max_events < 1:
            raise ValueError("diagnostic event bound must be positive")
        if type(event_retention_seconds) is not int or event_retention_seconds < 1:
            raise ValueError("diagnostic retention must be positive")
        self._store = store
        self._metrics = metrics or MetricsRegistry()
        self._collector = collector
        self._clock = clock
        self._max_events = max_events
        self._event_retention_seconds = event_retention_seconds
        self._ring_buffer = ring_buffer
        self._fingerprint_key = secrets.token_bytes(32)
        self._lock = RLock()
        self._in_flight_event_ids: set[str] = set()
        self._reported_ring_counts = (0, 0, 0)

    def new_correlation_id(self) -> str:
        return f"corr-{uuid.uuid4().hex}"

    def now(self) -> float:
        """Return the clock used for event retention and upload metadata."""
        return self._clock()

    def fingerprint(self, value: str) -> str:
        """Return a process-keyed opaque fingerprint, never the source value."""
        if type(value) is not str or not value:
            raise ValueError("fingerprint source must be a non-empty string")
        digest = hmac.new(
            self._fingerprint_key,
            value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:24]
        return f"fp-{digest}"

    def route_identity(self, value: str) -> str:
        """Return a process-keyed route identity without retaining its input."""
        if type(value) is not str or not value:
            raise ValueError("route identity source must be a non-empty string")
        digest = hmac.new(
            self._fingerprint_key,
            value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:24]
        return f"route-{digest}"

    def record(self, event: DiagnosticEvent | Mapping[str, object]) -> bool:
        """Accept a safe event, or drop it without interrupting live work."""
        try:
            safe_event = (
                event
                if isinstance(event, DiagnosticEvent)
                else DiagnosticEvent.from_mapping(event)
            )
        except DiagnosticValidationError:
            try:
                self._store.record_rejection()
            except Exception as error:  # noqa: BLE001 - diagnostics must not block live work
                del error
            self._metric_inc_safely(
                "hermes_home_diagnostics_events_rejected_total",
                labels={"reason": "schema"},
            )
            return False

        now = self._clock()
        safe_event = self._with_retention_deadline(safe_event)
        if _event_expired(safe_event, before=now):
            try:
                self._store.record_rejection()
            except Exception as error:  # noqa: BLE001 - diagnostics must not block live work
                del error
            self._metric_inc_safely(
                "hermes_home_diagnostics_events_rejected_total",
                labels={"reason": "retention"},
            )
            return False

        with self._lock:
            try:
                self._store.purge_expired(before=now)
                dropped = self._store.append(safe_event, max_events=self._max_events)
            except Exception:  # noqa: BLE001 - diagnostics must not block live work
                self._metric_inc_safely(
                    "hermes_home_diagnostics_events_rejected_total",
                    labels={"reason": "store"},
                )
                return False
            self._metric_inc_safely(
                "hermes_home_diagnostics_events_total",
                labels={"source": safe_event.source, "outcome": safe_event.outcome},
            )
            if dropped:
                self._metric_inc_safely("hermes_home_diagnostics_events_dropped_total")
            self._refresh_status_metrics_safely()
        return True

    def timeline(self, correlation_id: str) -> tuple[DiagnosticEvent, ...]:
        _require_token(correlation_id, "correlation_id")
        with self._lock:
            events = self._store.events(
                correlation_id=correlation_id,
                before=self._clock(),
            )
            self._refresh_status_metrics_safely()
            return events

    def status(self) -> DiagnosticsStatus:
        with self._lock:
            status = self._purged_status()
            self._refresh_status_metrics_safely(status)
            return status

    def flush(self, *, limit: int | None = None) -> UploadResult:
        """Try the injected collector without holding the recorder lock over I/O."""
        effective_limit = self._max_events if limit is None else limit
        if (
            type(effective_limit) is not int
            or not 1 <= effective_limit <= self._max_events
        ):
            raise ValueError("upload batch limit is outside the event bound")
        with self._lock:
            try:
                pending = self._store.pending_events(
                    limit=min(
                        self._max_events,
                        effective_limit + len(self._in_flight_event_ids),
                    ),
                    before=self._clock(),
                )
            except Exception:  # noqa: BLE001 - storage outage is isolated
                self._mark_collector_unreachable()
                return UploadResult(0, False)
            pending = tuple(
                event
                for event in pending
                if event.event_id not in self._in_flight_event_ids
            )[:effective_limit]
            if not pending:
                try:
                    status = self._store.status()
                except Exception:  # noqa: BLE001 - storage outage is isolated
                    self._mark_collector_unreachable()
                    return UploadResult(0, False)
                self._refresh_status_metrics_safely(status)
                return UploadResult(0, status.collector_reachable)
            if self._collector is None:
                self._mark_collector_unreachable()
                return UploadResult(0, False)
            self._in_flight_event_ids.update(event.event_id for event in pending)

        idempotency_key = _upload_idempotency_key(pending)
        try:
            acknowledgement = self._collector.upload(
                pending,
                idempotency_key=idempotency_key,
            )
            _require_upload_acknowledgement(
                acknowledgement,
                idempotency_key=idempotency_key,
                event_ids=tuple(event.event_id for event in pending),
            )
        except Exception:  # noqa: BLE001 - collector failure is isolated
            with self._lock:
                self._in_flight_event_ids.difference_update(
                    event.event_id for event in pending
                )
            self._mark_collector_unreachable()
            return UploadResult(0, False)

        uploaded_at = self._clock()
        try:
            with self._lock:
                self._store.mark_uploaded(
                    [event.event_id for event in pending],
                    uploaded_at=uploaded_at,
                )
        except Exception:  # noqa: BLE001 - storage outage is isolated
            self._mark_collector_unreachable()
            return UploadResult(0, False)
        finally:
            with self._lock:
                self._in_flight_event_ids.difference_update(
                    event.event_id for event in pending
                )
        self._metric_inc_safely(
            "hermes_home_diagnostics_uploads_total",
            labels={"outcome": "success"},
        )
        self._refresh_status_metrics_safely()
        return UploadResult(len(pending), True)

    def _mark_collector_unreachable(self) -> None:
        try:
            self._store.mark_collector_unreachable()
        except Exception as error:  # noqa: BLE001 - storage outage is isolated
            del error
        self._metric_inc_safely(
            "hermes_home_diagnostics_uploads_total",
            labels={"outcome": "unavailable"},
        )
        self._refresh_status_metrics_safely()

    def _purged_status(self) -> DiagnosticsStatus:
        self._store.purge_expired(before=self._clock())
        return self._status_with_ring(self._store.status())

    def _status_with_ring(self, status: DiagnosticsStatus) -> DiagnosticsStatus:
        if self._ring_buffer is None:
            return status
        ring_status = self._ring_buffer.status()
        return replace(
            status,
            ring_evicted_entry_count=ring_status.evicted_entry_count,
            ring_expired_entry_count=ring_status.expired_entry_count,
            ring_out_of_order_drop_count=ring_status.out_of_order_drop_count,
        )

    def _with_retention_deadline(self, event: DiagnosticEvent) -> DiagnosticEvent:
        if event.retention_deadline is not None:
            return event
        seconds = (
            self._event_retention_seconds
            if event.retention_class == "events"
            else _RETENTION_SECONDS[event.retention_class]
        )
        try:
            deadline = float(event.occurred_at) + seconds
        except (OverflowError, TypeError, ValueError) as error:
            raise DiagnosticValidationError(
                "event retention deadline cannot be represented"
            ) from error
        return replace(event, retention_deadline=deadline)

    def _metric_inc_safely(
        self,
        name: str,
        *,
        labels: Mapping[str, str] | None = None,
        value: float = 1.0,
    ) -> None:
        try:
            self._metrics.inc(name, labels=labels, value=value)
        except Exception as error:  # noqa: BLE001 - metrics are best effort
            del error

    def _refresh_status_metrics_safely(
        self, status: DiagnosticsStatus | None = None
    ) -> None:
        try:
            self._refresh_status_metrics(status)
        except Exception as error:  # noqa: BLE001 - diagnostics must not block live work
            del error

    def _refresh_status_metrics(self, status: DiagnosticsStatus | None = None) -> None:
        current = self._status_with_ring(status or self._store.status())
        self._metric_set_safely(
            "hermes_home_diagnostics_queue_depth",
            current.queued_event_count,
        )
        self._metric_set_safely(
            "hermes_home_diagnostics_collector_reachable",
            int(current.collector_reachable),
        )
        ring_counts = (
            current.ring_evicted_entry_count,
            current.ring_expired_entry_count,
            current.ring_out_of_order_drop_count,
        )
        ring_metric_names = (
            "hermes_home_diagnostics_ring_entries_evicted_total",
            "hermes_home_diagnostics_ring_entries_expired_total",
            "hermes_home_diagnostics_ring_entries_out_of_order_total",
        )
        previous_ring_counts = self._reported_ring_counts
        for name, previous, value in zip(
            ring_metric_names,
            previous_ring_counts,
            ring_counts,
            strict=True,
        ):
            delta = value - previous
            if delta > 0:
                self._metric_inc_safely(name, value=delta)
        self._reported_ring_counts = ring_counts
        if current.last_successful_upload_at is not None:
            self._metric_set_safely(
                "hermes_home_diagnostics_last_upload_timestamp_seconds",
                current.last_successful_upload_at,
            )

    def _metric_set_safely(self, name: str, value: float) -> None:
        try:
            self._metrics.set(name, value)
        except Exception as error:  # noqa: BLE001 - metrics are best effort
            del error


class CaptureStateError(ValueError):
    """Raised when an incident capture cannot make the requested transition."""


@dataclass(frozen=True, slots=True)
class CaptureScope:
    """The fixed opaque endpoint and current task/session capture scope."""

    endpoint_fingerprint: str
    task_fingerprint: str

    def __post_init__(self) -> None:
        _require_token(self.endpoint_fingerprint, "endpoint_fingerprint")
        _require_token(self.task_fingerprint, "task_fingerprint")


@dataclass(frozen=True, slots=True)
class RingBufferEntry:
    """One private local evidence item; never part of automatic telemetry."""

    captured_at: float
    evidence: bytes = field(repr=False)
    evidence_id: str = field(default_factory=lambda: f"evidence-{uuid.uuid4().hex}")

    def __post_init__(self) -> None:
        _require_timestamp(self.captured_at, "captured_at")
        if type(self.evidence) is not bytes:
            raise TypeError("ring-buffer evidence must be bytes")
        _require_token(self.evidence_id, "evidence_id")


@dataclass(frozen=True, slots=True)
class RingBufferStats:
    """Safe counters describing evidence that left the bounded ring."""

    evicted_entry_count: int = 0
    expired_entry_count: int = 0
    out_of_order_drop_count: int = 0


class LocalRingBuffer:
    """Bounded, scope-aware local evidence buffer with a sixty-second default."""

    def __init__(
        self,
        *,
        max_age_seconds: float = 60.0,
        max_entries: int = 256,
        max_bytes: int = 1_048_576,
    ) -> None:
        if not _positive_number(max_age_seconds):
            raise ValueError("ring-buffer age must be positive")
        if type(max_entries) is not int or max_entries < 1:
            raise ValueError("ring-buffer entry bound must be positive")
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("ring-buffer byte bound must be positive")
        self._max_age_seconds = float(max_age_seconds)
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._entries: deque[tuple[CaptureScope, RingBufferEntry]] = deque()
        self._total_bytes = 0
        self._evicted_entry_count = 0
        self._expired_entry_count = 0
        self._out_of_order_drop_count = 0
        self._lock = RLock()

    def append(
        self,
        scope: CaptureScope,
        evidence: bytes,
        *,
        captured_at: float,
    ) -> None:
        if not isinstance(scope, CaptureScope):
            raise TypeError("ring-buffer scope must be a CaptureScope")
        entry = RingBufferEntry(captured_at, evidence)
        if len(entry.evidence) > self._max_bytes:
            raise ValueError("ring-buffer evidence exceeds byte bound")
        with self._lock:
            latest_captured_at = max(
                (existing.captured_at for _, existing in self._entries),
                default=entry.captured_at,
            )
            before = latest_captured_at - self._max_age_seconds
            self._purge(before)
            if entry.captured_at < before:
                self._out_of_order_drop_count += 1
                return
            while self._entries and (
                len(self._entries) >= self._max_entries
                or self._total_bytes + len(entry.evidence) > self._max_bytes
            ):
                _, removed = self._entries.popleft()
                self._total_bytes -= len(removed.evidence)
                self._evicted_entry_count += 1
            self._entries.append((scope, entry))
            self._total_bytes += len(entry.evidence)

    def entries(
        self,
        scope: CaptureScope,
        *,
        now: float,
    ) -> tuple[RingBufferEntry, ...]:
        if not isinstance(scope, CaptureScope):
            raise TypeError("ring-buffer scope must be a CaptureScope")
        _require_timestamp(now, "now")
        with self._lock:
            self._purge(now - self._max_age_seconds)
            return tuple(
                sorted(
                    (
                        entry
                        for entry_scope, entry in self._entries
                        if entry_scope == scope
                    ),
                    key=lambda entry: (entry.captured_at, entry.evidence_id),
                )
            )

    def status(self) -> RingBufferStats:
        with self._lock:
            return RingBufferStats(
                evicted_entry_count=self._evicted_entry_count,
                expired_entry_count=self._expired_entry_count,
                out_of_order_drop_count=self._out_of_order_drop_count,
            )

    def _purge(self, before: float) -> None:
        retained: deque[tuple[CaptureScope, RingBufferEntry]] = deque()
        for scope, entry in self._entries:
            if entry.captured_at < before:
                self._total_bytes -= len(entry.evidence)
                self._expired_entry_count += 1
            else:
                retained.append((scope, entry))
        self._entries = retained


@dataclass(frozen=True, slots=True)
class CaptureEvidenceDescriptor:
    """Safe preview metadata for one selectable private evidence item."""

    evidence_id: str
    captured_at: float
    byte_count: int

    def __post_init__(self) -> None:
        _require_token(self.evidence_id, "evidence_id")
        _require_timestamp(self.captured_at, "captured_at")
        _require_count(self.byte_count, "byte_count")

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "captured_at": self.captured_at,
            "byte_count": self.byte_count,
        }


@dataclass(frozen=True, slots=True)
class CapturePreview:
    """Safe metadata shown before private evidence is sealed or uploaded."""

    capture_id: str
    scope: CaptureScope
    state: str
    entry_count: int
    total_bytes: int
    oldest_captured_at: float | None
    newest_captured_at: float | None
    evidence: tuple[CaptureEvidenceDescriptor, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": DIAGNOSTICS_SCHEMA,
            "capture_id": self.capture_id,
            "scope": {
                "endpoint_fingerprint": self.scope.endpoint_fingerprint,
                "task_fingerprint": self.scope.task_fingerprint,
            },
            "state": self.state,
            "entry_count": self.entry_count,
            "total_bytes": self.total_bytes,
            "oldest_captured_at": self.oldest_captured_at,
            "newest_captured_at": self.newest_captured_at,
            "evidence": [descriptor.to_dict() for descriptor in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class CaptureRecord:
    """Public-safe incident state; encrypted evidence is deliberately hidden."""

    capture_id: str
    scope: CaptureScope
    state: str
    created_at: float
    expires_at: float
    retention_deadline: float | None = None
    failure_code: str | None = None
    entry_count: int = 0
    total_bytes: int = 0
    encrypted_payload: bytes | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": DIAGNOSTICS_SCHEMA,
            "capture_id": self.capture_id,
            "scope": {
                "endpoint_fingerprint": self.scope.endpoint_fingerprint,
                "task_fingerprint": self.scope.task_fingerprint,
            },
            "state": self.state,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "retention_deadline": self.retention_deadline,
            "entry_count": self.entry_count,
            "total_bytes": self.total_bytes,
        }
        if self.failure_code is not None:
            payload["failure_code"] = self.failure_code
        return payload


@dataclass(frozen=True, slots=True)
class CaptureAuditRecord:
    """Non-content audit evidence for an incident lifecycle operation."""

    capture_id: str
    action: str
    occurred_at: float
    outcome: str


@dataclass(frozen=True, slots=True)
class IncidentBundle:
    """Private sealed evidence handed to the separately injected upload port."""

    capture_id: str
    scope: CaptureScope
    encrypted_payload: bytes = field(repr=False)
    retention_deadline: float


class IncidentSealer(Protocol):
    """Port that seals selected private evidence before it leaves Home."""

    def seal(
        self,
        evidence: Sequence[bytes],
        *,
        capture_id: str,
        scope: CaptureScope,
    ) -> bytes: ...


class IncidentUploader(Protocol):
    """Port for a separate encrypted incident-bundle store."""

    def upload(self, bundle: IncidentBundle) -> None: ...


class IncidentBundleLifecycle(Protocol):
    """Optional remote preserve/delete contract for an injected bundle store."""

    def preserve(self, capture_id: str) -> None: ...

    def delete(self, capture_id: str) -> None: ...


class CaptureAuthorizer(Protocol):
    """Authorize a reviewer to operate on a fixed capture scope."""

    def authorize(self, scope: CaptureScope) -> bool: ...


class CurrentTaskResolver(Protocol):
    """Resolve the endpoint/task scope that is current at operation time."""

    def current_scope(self) -> CaptureScope | None: ...


class IncidentCaptureService:
    """Apply the explicit, scope-fixed incident-capture state machine."""

    def __init__(
        self,
        *,
        ring_buffer: LocalRingBuffer,
        sealer: IncidentSealer | None,
        uploader: IncidentUploader | None,
        clock: Callable[[], float] = time.time,
        capture_ttl_seconds: int = 300,
        authorizer: CaptureAuthorizer | None = None,
        current_task_resolver: CurrentTaskResolver | None = None,
        capture_store: IncidentCaptureStore | None = None,
        bundle_lifecycle: IncidentBundleLifecycle | None = None,
        expiry_scheduler: CaptureExpiryScheduler | None = None,
        max_capture_records: int = 1024,
        max_audit_records: int = 4096,
    ) -> None:
        if type(capture_ttl_seconds) is not int or capture_ttl_seconds < 1:
            raise ValueError("capture TTL must be positive")
        if type(max_capture_records) is not int or max_capture_records < 1:
            raise ValueError("capture record bound must be positive")
        if type(max_audit_records) is not int or max_audit_records < 1:
            raise ValueError("capture audit bound must be positive")
        self._ring_buffer = ring_buffer
        self._sealer = sealer
        self._uploader = uploader
        self._clock = clock
        self._capture_ttl_seconds = capture_ttl_seconds
        self._authorizer = authorizer
        self._current_task_resolver = current_task_resolver
        self._capture_store = capture_store
        self._bundle_lifecycle = bundle_lifecycle
        self._max_capture_records = max_capture_records
        self._max_audit_records = max_audit_records
        self._captures: dict[str, CaptureRecord] = (
            {capture.capture_id: capture for capture in capture_store.captures()}
            if capture_store is not None
            else {}
        )
        self._evidence: dict[str, tuple[RingBufferEntry, ...]] = {}
        self._audit: list[CaptureAuditRecord] = list(
            capture_store.audit_log() if capture_store is not None else ()
        )[-max_audit_records:]
        self._lock = RLock()
        self._expiry_scheduler = expiry_scheduler
        self._expiry_scheduler_handle: object | None = None
        if expiry_scheduler is not None:
            self._expiry_scheduler_handle = expiry_scheduler.register(
                self.purge_expired,
                interval_seconds=min(float(capture_ttl_seconds), 60.0),
            )

    def arm(self, scope: CaptureScope) -> CaptureRecord:
        if not isinstance(scope, CaptureScope):
            raise TypeError("capture scope must be a CaptureScope")
        self._authorize_scope(scope)
        now = self._clock()
        capture = CaptureRecord(
            capture_id=f"capture-{uuid.uuid4().hex}",
            scope=scope,
            state="armed",
            created_at=now,
            expires_at=now + self._capture_ttl_seconds,
        )
        with self._lock:
            self._captures[capture.capture_id] = capture
            self._persist_capture(capture)
            self._audit_event(capture, "arm", "accepted", now=now)
            self._trim_memory()
        return capture

    def preview(
        self,
        capture_id: str,
        *,
        scope: CaptureScope | None = None,
    ) -> CapturePreview:
        with self._lock:
            capture = self._active_capture(capture_id)
            self._require_scope(capture, scope)
            self._authorize_scope(capture.scope)
            if capture.state not in {"armed", "previewing", "failed"}:
                raise CaptureStateError(
                    f"capture cannot be previewed from {capture.state}"
                )
            entries = self._ring_buffer.entries(capture.scope, now=self._clock())
            self._evidence[capture.capture_id] = entries
            descriptors = tuple(
                CaptureEvidenceDescriptor(
                    evidence_id=entry.evidence_id,
                    captured_at=entry.captured_at,
                    byte_count=len(entry.evidence),
                )
                for entry in entries
            )
            capture = replace(
                capture,
                state="previewing",
                failure_code=None,
                entry_count=len(entries),
                total_bytes=sum(len(entry.evidence) for entry in entries),
            )
            self._captures[capture.capture_id] = capture
            self._persist_capture(capture)
            self._audit_event(capture, "preview", "accepted")
            return CapturePreview(
                capture_id=capture.capture_id,
                scope=capture.scope,
                state=capture.state,
                entry_count=capture.entry_count,
                total_bytes=capture.total_bytes,
                oldest_captured_at=(entries[0].captured_at if entries else None),
                newest_captured_at=(entries[-1].captured_at if entries else None),
                evidence=descriptors,
            )

    def approve(
        self,
        capture_id: str,
        *,
        evidence_ids: Sequence[str] | None = None,
    ) -> CaptureRecord:
        with self._lock:
            capture = self._active_capture(capture_id)
            self._authorize_scope(capture.scope)
            if capture.state != "previewing":
                raise CaptureStateError(
                    f"capture cannot be approved from {capture.state}"
                )
            if evidence_ids is None:
                raise CaptureStateError("explicit evidence selection is required")
            entries = self._select_evidence(capture.capture_id, evidence_ids)
            capture = replace(
                capture,
                state="encrypting",
                entry_count=len(entries),
                total_bytes=sum(len(entry.evidence) for entry in entries),
            )
            self._captures[capture.capture_id] = capture
            self._persist_capture(capture)
            if self._sealer is None:
                return self._fail(capture, "encryption_unavailable")
            try:
                encrypted = self._sealer.seal(
                    tuple(entry.evidence for entry in entries),
                    capture_id=capture.capture_id,
                    scope=capture.scope,
                )
                if type(encrypted) is not bytes or not encrypted:
                    raise ValueError("sealer returned no encrypted payload")
            except Exception:  # noqa: BLE001 - sealing failure is typed
                return self._fail(capture, "encryption_failed")
            capture = self._active_capture(capture.capture_id)
            capture = replace(
                capture,
                state="uploading",
                encrypted_payload=encrypted,
            )
            self._captures[capture.capture_id] = capture
            self._persist_capture(capture)
            if self._uploader is None:
                return self._fail(capture, "upload_unavailable")
            capture = self._active_capture(capture.capture_id)
            deadline = self._clock() + INCIDENT_RETENTION_SECONDS
            bundle = IncidentBundle(
                capture_id=capture.capture_id,
                scope=capture.scope,
                encrypted_payload=encrypted,
                retention_deadline=deadline,
            )
            try:
                self._uploader.upload(bundle)
            except Exception:  # noqa: BLE001 - upload failure is typed
                return self._fail(capture, "upload_failed")
            capture = replace(
                capture,
                state="uploaded",
                retention_deadline=deadline,
                encrypted_payload=None,
            )
            self._captures[capture.capture_id] = capture
            self._evidence.pop(capture.capture_id, None)
            self._persist_capture(capture)
            self._audit_event(capture, "approve", "uploaded")
            return capture

    def cancel(self, capture_id: str) -> CaptureRecord:
        with self._lock:
            capture = self._active_capture(capture_id)
            self._authorize_scope(capture.scope)
            if capture.state not in {"armed", "previewing", "failed"}:
                raise CaptureStateError(
                    f"capture cannot be cancelled from {capture.state}"
                )
            capture = replace(capture, state="cancelled")
            self._captures[capture.capture_id] = capture
            self._evidence.pop(capture.capture_id, None)
            self._persist_capture(capture)
            self._audit_event(capture, "cancel", "cancelled")
            return capture

    def preserve(self, capture_id: str) -> CaptureRecord:
        with self._lock:
            capture = self._active_capture(capture_id)
            self._authorize_scope(capture.scope)
            if capture.state != "uploaded":
                raise CaptureStateError(
                    f"capture cannot be preserved from {capture.state}"
                )
            if self._bundle_lifecycle is not None:
                try:
                    self._bundle_lifecycle.preserve(capture.capture_id)
                except Exception as error:
                    self._audit_event(capture, "preserve", "failed")
                    raise CaptureStateError("remote preserve failed") from error
            capture = replace(capture, state="preserved", retention_deadline=None)
            self._captures[capture.capture_id] = capture
            self._persist_capture(capture)
            self._audit_event(capture, "preserve", "preserved")
            return capture

    def delete(self, capture_id: str) -> CaptureRecord:
        with self._lock:
            capture = self._active_capture(capture_id, expire=False)
            self._authorize_scope(capture.scope)
            if capture.state not in {"uploaded", "preserved", "failed", "cancelled"}:
                raise CaptureStateError(
                    f"capture cannot be deleted from {capture.state}"
                )
            if self._bundle_lifecycle is not None and capture.state in {
                "uploaded",
                "preserved",
            }:
                try:
                    self._bundle_lifecycle.delete(capture.capture_id)
                except Exception as error:
                    self._audit_event(capture, "delete", "failed")
                    raise CaptureStateError("remote delete failed") from error
            capture = replace(
                capture,
                state="deleted",
                retention_deadline=None,
                encrypted_payload=None,
            )
            self._captures[capture.capture_id] = capture
            self._evidence.pop(capture.capture_id, None)
            self._persist_capture(capture)
            self._audit_event(capture, "delete", "deleted")
            return capture

    def get(self, capture_id: str) -> CaptureRecord:
        with self._lock:
            capture = self._active_capture(capture_id, expire=True)
            self._authorize_scope(capture.scope)
            return capture

    def audit_log(self) -> tuple[CaptureAuditRecord, ...]:
        with self._lock:
            return tuple(self._audit)

    def purge_expired(self) -> int:
        """Expire all due captures; intended to be called by the runtime reaper."""
        with self._lock:
            now = self._clock()
            expired = 0
            for capture in tuple(self._captures.values()):
                if self._capture_is_expired(capture, now=now):
                    self._expire_capture(capture, now=now)
                    expired += 1
            return expired

    def close(self) -> None:
        """Detach the runtime-owned expiry callback, if one was registered."""
        if (
            self._expiry_scheduler is not None
            and self._expiry_scheduler_handle is not None
        ):
            self._expiry_scheduler.unregister(self._expiry_scheduler_handle)
            self._expiry_scheduler_handle = None

    def _active_capture(self, capture_id: str, *, expire: bool = True) -> CaptureRecord:
        _require_token(capture_id, "capture_id")
        capture = self._captures.get(capture_id)
        if capture is None:
            raise CaptureStateError("capture not found")
        if not expire:
            return capture
        now = self._clock()
        if self._capture_is_expired(capture, now=now):
            self._expire_capture(capture, now=now)
            raise CaptureStateError("capture expired")
        return capture

    @staticmethod
    def _capture_is_expired(capture: CaptureRecord, *, now: float) -> bool:
        capture_expired = (
            capture.state
            in {"armed", "previewing", "failed", "encrypting", "uploading"}
            and now >= capture.expires_at
        )
        retention_expired = (
            capture.state == "uploaded"
            and capture.retention_deadline is not None
            and now >= capture.retention_deadline
        )
        return capture_expired or retention_expired

    def _expire_capture(self, capture: CaptureRecord, *, now: float) -> CaptureRecord:
        expired = replace(
            capture,
            state="expired",
            retention_deadline=None,
            encrypted_payload=None,
        )
        self._captures[capture.capture_id] = expired
        self._evidence.pop(capture.capture_id, None)
        self._persist_capture(expired)
        self._audit_event(expired, "expire", "expired", now=now)
        return expired

    @staticmethod
    def _require_scope(
        capture: CaptureRecord,
        scope: CaptureScope | None,
    ) -> None:
        if scope is not None and scope != capture.scope:
            raise CaptureStateError("capture scope does not match")

    def _authorize_scope(self, scope: CaptureScope) -> None:
        if self._authorizer is None or self._current_task_resolver is None:
            raise CaptureStateError("capture authorization is unavailable")
        try:
            authorized = self._authorizer.authorize(scope)
            current_scope = self._current_task_resolver.current_scope()
        except Exception as error:
            raise CaptureStateError("capture authorization is unavailable") from error
        if authorized is not True:
            raise CaptureStateError("capture is not authorized")
        if current_scope != scope:
            raise CaptureStateError("capture scope is not current")

    def _select_evidence(
        self,
        capture_id: str,
        evidence_ids: Sequence[str],
    ) -> tuple[RingBufferEntry, ...]:
        if isinstance(evidence_ids, (str, bytes)):
            raise CaptureStateError("evidence selection must be a sequence of IDs")
        selected_ids = tuple(evidence_ids)
        if any(type(evidence_id) is not str for evidence_id in selected_ids):
            raise CaptureStateError("evidence selection contains an invalid ID")
        if len(set(selected_ids)) != len(selected_ids):
            raise CaptureStateError("evidence selection contains duplicate IDs")
        entries = self._evidence.get(capture_id)
        if entries is None:
            raise CaptureStateError("capture evidence is unavailable")
        by_id = {entry.evidence_id: entry for entry in entries}
        if any(evidence_id not in by_id for evidence_id in selected_ids):
            raise CaptureStateError("evidence selection is outside the preview")
        return tuple(by_id[evidence_id] for evidence_id in selected_ids)

    def _fail(self, capture: CaptureRecord, code: str) -> CaptureRecord:
        capture = replace(
            capture,
            state="failed",
            failure_code=code,
            encrypted_payload=None,
        )
        self._captures[capture.capture_id] = capture
        self._evidence.pop(capture.capture_id, None)
        self._persist_capture(capture)
        self._audit_event(capture, "approve", code)
        return capture

    def _persist_capture(self, capture: CaptureRecord) -> None:
        if self._capture_store is not None:
            self._capture_store.save_capture(capture)

    def _trim_memory(self) -> None:
        while len(self._captures) > self._max_capture_records:
            terminal = [
                capture
                for capture in self._captures.values()
                if capture.state
                not in {"armed", "previewing", "encrypting", "uploading"}
            ]
            candidates = terminal or list(self._captures.values())
            oldest = min(candidates, key=lambda capture: capture.created_at)
            self._captures.pop(oldest.capture_id, None)
            self._evidence.pop(oldest.capture_id, None)

        if len(self._audit) > self._max_audit_records:
            del self._audit[: -self._max_audit_records]

    def _audit_event(
        self,
        capture: CaptureRecord,
        action: str,
        outcome: str,
        *,
        now: float | None = None,
    ) -> None:
        record = CaptureAuditRecord(
            capture_id=capture.capture_id,
            action=action,
            occurred_at=self._clock() if now is None else now,
            outcome=outcome,
        )
        self._audit.append(record)
        if self._capture_store is not None:
            self._capture_store.append_audit(record)
        self._trim_memory()


def _require_token(value: object, name: str, *, max_length: int = 128) -> None:
    if type(value) is not str or not 1 <= len(value) <= max_length:
        raise DiagnosticValidationError(f"{name} must be a bounded token")
    if any(character in value for character in "\r\n"):
        raise DiagnosticValidationError(f"{name} must not contain line breaks")
    if any(not (character.isalnum() or character in "._:-+") for character in value):
        raise DiagnosticValidationError(f"{name} contains unsafe characters")


def _require_fingerprint(value: object, name: str) -> None:
    if type(value) is not str or not re.fullmatch(r"fp-[0-9a-f]{24}", value):
        raise DiagnosticValidationError(f"{name} must be an opaque fingerprint")


def _require_route_id(value: object) -> None:
    _require_token(value, "route_id")
    if value in _SAFE_ROUTE_IDS:
        return
    if type(value) is str and re.fullmatch(r"route-[0-9a-f]{24}", value):
        return
    raise DiagnosticValidationError("route ID is not an approved safe identity")


def _require_service_version(value: object) -> None:
    if type(value) is not str or not re.fullmatch(
        r"v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", value
    ):
        raise DiagnosticValidationError("service version is not safe")


def _event_expired(event: DiagnosticEvent, *, before: float) -> bool:
    deadline = event.retention_deadline
    if deadline is None:
        deadline = event.occurred_at + _RETENTION_SECONDS[event.retention_class]
    return deadline <= before


def _upload_idempotency_key(events: Sequence[DiagnosticEvent]) -> str:
    event_ids = "\0".join(event.event_id for event in events)
    digest = hashlib.sha256(event_ids.encode("utf-8")).hexdigest()[:32]
    return f"upload-{digest}"


def _require_upload_acknowledgement(
    acknowledgement: object,
    *,
    idempotency_key: str,
    event_ids: tuple[str, ...],
) -> None:
    if not isinstance(acknowledgement, UploadAcknowledgement):
        raise DiagnosticValidationError("collector did not acknowledge the upload")
    if acknowledgement.idempotency_key != idempotency_key:
        raise DiagnosticValidationError("collector acknowledgement key mismatches")
    if acknowledgement.event_ids != event_ids:
        raise DiagnosticValidationError("collector acknowledgement IDs mismatch")


def _positive_number(value: object) -> bool:
    return (
        type(value) in (int, float)
        and not isinstance(value, bool)
        and isfinite(float(value))
        and float(value) > 0
    )


def _require_timestamp(value: object, name: str) -> None:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise DiagnosticValidationError(f"{name} must be numeric")
    try:
        numeric_value = float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise DiagnosticValidationError(
            f"{name} must be finite and non-negative"
        ) from error
    if not isfinite(numeric_value) or numeric_value < 0:
        raise DiagnosticValidationError(f"{name} must be finite and non-negative")


def _require_count(value: object, name: str) -> None:
    if type(value) is not int or value < 0:
        raise DiagnosticValidationError(f"{name} must be a non-negative integer")
