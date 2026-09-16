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
MAX_SAFE_COUNT = 2**63 - 1
MAX_SERVICE_VERSION_LENGTH = 64

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
_CAPTURE_STATES = frozenset(
    {
        "armed",
        "previewing",
        "approved",
        "encrypting",
        "uploading",
        "uploaded",
        "preserved",
        "failed",
        "cancelled",
        "expired",
        "deleted",
    }
)
_CAPTURE_ACTIONS = frozenset(
    {"arm", "preview", "approve", "cancel", "preserve", "delete", "expire"}
)
_CAPTURE_AUDIT_OUTCOMES = frozenset(
    {
        "accepted",
        "approved",
        "cancelled",
        "deleted",
        "encryption_failed",
        "encryption_unavailable",
        "expired",
        "failed",
        "preserved",
        "upload_failed",
        "upload_unavailable",
        "uploaded",
    }
)
_CAPTURE_AUDIT_ALLOWED_OUTCOMES = {
    "arm": frozenset({"accepted"}),
    "preview": frozenset({"accepted"}),
    "approve": frozenset(
        {
            "approved",
            "accepted",
            "failed",
            "encryption_failed",
            "encryption_unavailable",
            "upload_failed",
            "upload_unavailable",
            "uploaded",
        }
    ),
    "cancel": frozenset({"cancelled"}),
    "preserve": frozenset({"preserved", "failed"}),
    "delete": frozenset({"deleted", "failed"}),
    "expire": frozenset({"expired"}),
}
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
        _require_opaque_id(self.event_id, "event_id", prefix="evt-")
        _require_opaque_id(self.correlation_id, "correlation_id", prefix="corr-")
        _require_choice(self.source, "source", _SOURCES)
        _require_choice(self.phase, "phase", _PHASES)
        _require_choice(self.outcome, "outcome", _OUTCOMES)
        _require_timestamp(self.occurred_at, "occurred_at")
        if self.duration_ms is not None:
            _require_count(self.duration_ms, "duration_ms")
        if self.failure_code is not None:
            _require_choice(self.failure_code, "failure code", _FAILURE_CODES)
        if self.route_class is not None:
            _require_choice(self.route_class, "route class", _ROUTE_CLASSES)
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
        if self.health is not None:
            _require_choice(self.health, "health value", _HEALTH_VALUES)
        for value, name in (
            (self.byte_count, "byte_count"),
            (self.segment_count, "segment_count"),
            (self.queue_depth, "queue_depth"),
        ):
            if value is not None:
                _require_count(value, name)
        if self.upload_outcome is not None:
            _require_choice(self.upload_outcome, "upload outcome", _UPLOAD_OUTCOMES)
        _require_choice(self.retention_class, "retention class", _RETENTION_CLASSES)
        if self.retention_deadline is not None:
            _require_timestamp(self.retention_deadline, "retention_deadline")
            try:
                maximum_deadline = (
                    self.occurred_at + _RETENTION_SECONDS[self.retention_class]
                )
            except (OverflowError, TypeError, ValueError) as error:
                raise DiagnosticValidationError(
                    "event retention deadline cannot be represented"
                ) from error
            if self.retention_deadline > maximum_deadline:
                raise DiagnosticValidationError(
                    "event retention deadline exceeds its retention class"
                )

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
        if event_id is not None:
            raise DiagnosticValidationError("event_id is generated by Home")
        payload: dict[str, object] = {
            "schema": DIAGNOSTICS_SCHEMA,
            "event_id": f"evt-{uuid.uuid4().hex}",
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
    event_retention_seconds: int = EVENT_RETENTION_SECONDS

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
                "events": self.event_retention_seconds,
                "incident": INCIDENT_RETENTION_SECONDS,
            },
        }


@dataclass(frozen=True, slots=True)
class UploadResult:
    """The honest outcome of one explicit safe-event upload attempt."""

    uploaded_count: int
    collector_reachable: bool
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class UploadAcknowledgement:
    """Remote acknowledgement that one stable event batch was accepted."""

    idempotency_key: str
    event_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_token(self.idempotency_key, "idempotency_key")
        if not self.event_ids or any(
            type(event_id) is not str or not re.fullmatch(r"evt-[0-9a-f]{32}", event_id)
            for event_id in self.event_ids
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

    def append(
        self,
        event: DiagnosticEvent,
        *,
        max_events: int,
        before: float | None = None,
    ) -> bool: ...

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

    def status(self, *, before: float | None = None) -> DiagnosticsStatus: ...

    def record_rejection(self) -> None: ...

    def mark_collector_unreachable(self) -> None: ...


class IncidentCaptureStore(Protocol):
    """Durable metadata and audit port for explicit incident captures."""

    def captures(self) -> tuple[CaptureRecord, ...]: ...

    def save_capture(self, capture: CaptureRecord) -> None: ...

    def save_capture_and_audit(
        self, capture: CaptureRecord, record: CaptureAuditRecord
    ) -> None: ...

    def remove_capture(self, capture_id: str) -> None: ...

    def remove_capture_and_audit(
        self, capture_id: str, record: CaptureAuditRecord
    ) -> None: ...

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

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._lock = RLock()
        self._clock = clock
        self._events: deque[DiagnosticEvent] = deque()
        self._uploaded: set[str] = set()
        self._dropped_event_count = 0
        self._rejected_event_count = 0
        self._last_successful_upload_at: float | None = None
        self._collector_reachable = False

    def append(
        self,
        event: DiagnosticEvent,
        *,
        max_events: int,
        before: float | None = None,
    ) -> bool:
        if type(max_events) is not int or not 1 <= max_events <= MAX_SAFE_COUNT:
            raise ValueError("diagnostic event bound must be positive")
        current = self._clock() if before is None else before
        _require_timestamp(current, "diagnostic store clock")
        with self._lock:
            self._purge_expired(float(current))
            if _event_expired(event, before=float(current)):
                return False
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
        _require_timestamp(before, "diagnostic events clock")
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
        if type(limit) is not int or not 1 <= limit <= MAX_SAFE_COUNT:
            raise ValueError("diagnostic upload limit must be positive")
        _require_timestamp(before, "diagnostic pending clock")
        with self._lock:
            self._purge_expired(before)
            return tuple(
                event for event in self._events if event.event_id not in self._uploaded
            )[:limit]

    def mark_uploaded(self, event_ids: Sequence[str], *, uploaded_at: float) -> None:
        _require_timestamp(uploaded_at, "uploaded_at")
        with self._lock:
            requested = tuple(event_ids)
            if len(set(requested)) != len(requested):
                raise DiagnosticStoreError("duplicate diagnostic event IDs")
            known = {event.event_id for event in self._events}
            if any(event_id not in known for event_id in requested):
                raise DiagnosticStoreError("diagnostic event ID is unknown")
            self._uploaded.update(requested)
            self._last_successful_upload_at = uploaded_at
            self._collector_reachable = True

    def mark_collector_unreachable(self) -> None:
        with self._lock:
            self._collector_reachable = False

    def purge_expired(self, *, before: float) -> int:
        _require_timestamp(before, "diagnostic purge clock")
        with self._lock:
            return self._purge_expired(before)

    def status(self, *, before: float | None = None) -> DiagnosticsStatus:
        with self._lock:
            current = self._clock() if before is None else before
            _require_timestamp(current, "diagnostic status clock")
            self._purge_expired(float(current))
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
        if type(max_events) is not int or not 1 <= max_events <= MAX_SAFE_COUNT:
            raise ValueError("diagnostic event bound must be positive")
        if (
            type(event_retention_seconds) is not int
            or not 1 <= event_retention_seconds <= EVENT_RETENTION_SECONDS
        ):
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
        self._last_known_collector_reachable = False

    def new_correlation_id(self) -> str:
        return f"corr-{uuid.uuid4().hex}"

    def now(self) -> float:
        """Return the clock used for event retention and upload metadata."""
        return self._clock_now()

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

        try:
            now = self._clock_now()
            safe_event = self._with_retention_deadline(safe_event)
        except DiagnosticValidationError:
            try:
                self._store.record_rejection()
            except Exception as error:  # noqa: BLE001 - diagnostics must not block live work
                del error
            self._metric_inc_safely(
                "hermes_home_diagnostics_events_rejected_total",
                labels={"reason": "retention"},
            )
            return False
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
                dropped = self._store.append(
                    safe_event,
                    max_events=self._max_events,
                    before=now,
                )
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
        _require_opaque_id(correlation_id, "correlation_id", prefix="corr-")
        with self._lock:
            events = self._store.events(
                correlation_id=correlation_id,
                before=self._clock_now(),
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
                    before=self._clock_now(),
                )
            except Exception:  # noqa: BLE001 - storage outage is isolated
                return self._storage_failure_result()
            pending = tuple(
                event
                for event in pending
                if event.event_id not in self._in_flight_event_ids
            )[:effective_limit]
            if not pending:
                try:
                    status = self._store.status(before=self._clock_now())
                except Exception:  # noqa: BLE001 - storage outage is isolated
                    return self._storage_failure_result()
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

        try:
            uploaded_at = self._clock_now()
            with self._lock:
                self._store.mark_uploaded(
                    [event.event_id for event in pending],
                    uploaded_at=uploaded_at,
                )
        except Exception:  # noqa: BLE001 - storage outage is isolated
            return self._storage_failure_result()
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

    def _storage_failure_result(self) -> UploadResult:
        """Report a local-store failure without falsifying collector health."""
        return UploadResult(
            0,
            self._last_known_collector_reachable,
            failure_code="storage_unavailable",
        )

    def _purged_status(self) -> DiagnosticsStatus:
        now = self._clock_now()
        self._store.purge_expired(before=now)
        return self._status_with_ring(self._store.status(before=now))

    def _status_with_ring(self, status: DiagnosticsStatus) -> DiagnosticsStatus:
        status = replace(
            status,
            event_retention_seconds=self._event_retention_seconds,
        )
        if self._ring_buffer is None:
            return status
        ring_status = self._ring_buffer.status(now=self._clock_now())
        return replace(
            status,
            ring_evicted_entry_count=ring_status.evicted_entry_count,
            ring_expired_entry_count=ring_status.expired_entry_count,
            ring_out_of_order_drop_count=ring_status.out_of_order_drop_count,
        )

    def _with_retention_deadline(self, event: DiagnosticEvent) -> DiagnosticEvent:
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
        if event.retention_deadline is not None:
            if event.retention_deadline > deadline:
                raise DiagnosticValidationError(
                    "event retention deadline exceeds recorder retention"
                )
            return event
        return replace(event, retention_deadline=deadline)

    def _clock_now(self) -> float:
        now = self._clock()
        _require_timestamp(now, "clock")
        return float(now)

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
        current = self._status_with_ring(
            status or self._store.status(before=self._clock_now())
        )
        self._last_known_collector_reachable = current.collector_reachable
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
        _require_fingerprint(self.endpoint_fingerprint, "endpoint_fingerprint")
        _require_fingerprint(self.task_fingerprint, "task_fingerprint")


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
        _require_opaque_id(self.evidence_id, "evidence_id", prefix="evidence-")


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
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not _positive_number(max_age_seconds):
            raise ValueError("ring-buffer age must be positive")
        if type(max_entries) is not int or not 1 <= max_entries <= MAX_SAFE_COUNT:
            raise ValueError("ring-buffer entry bound must be positive")
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_SAFE_COUNT:
            raise ValueError("ring-buffer byte bound must be positive")
        self._max_age_seconds = float(max_age_seconds)
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._clock = clock
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
            now = self._clock()
            _require_timestamp(now, "ring-buffer clock")
            if entry.captured_at > now:
                raise ValueError("ring-buffer evidence cannot be from the future")
            before = now - self._max_age_seconds
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

    def status(self, *, now: float | None = None) -> RingBufferStats:
        with self._lock:
            current = self._clock() if now is None else now
            _require_timestamp(current, "now")
            self._purge(float(current) - self._max_age_seconds)
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
        _require_opaque_id(self.evidence_id, "evidence_id", prefix="evidence-")
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

    def __post_init__(self) -> None:
        _require_opaque_id(self.capture_id, "capture_id", prefix="capture-")
        if not isinstance(self.scope, CaptureScope):
            raise DiagnosticValidationError("capture scope is invalid")
        _require_choice(self.state, "capture state", _CAPTURE_STATES)
        _require_timestamp(self.created_at, "created_at")
        _require_timestamp(self.expires_at, "expires_at")
        if self.expires_at < self.created_at:
            raise DiagnosticValidationError("capture expiry precedes creation")
        if self.retention_deadline is not None:
            _require_timestamp(self.retention_deadline, "retention_deadline")
            if self.retention_deadline < self.created_at:
                raise DiagnosticValidationError(
                    "capture retention deadline precedes creation"
                )
        if self.state == "uploaded" and self.retention_deadline is None:
            raise DiagnosticValidationError(
                "uploaded capture must have a retention deadline"
            )
        if self.state == "preserved" and self.retention_deadline is not None:
            raise DiagnosticValidationError(
                "preserved capture cannot have a retention deadline"
            )
        if self.failure_code is not None:
            _require_choice(self.failure_code, "failure code", _FAILURE_CODES)
        if self.state == "failed" and self.failure_code is None:
            raise DiagnosticValidationError("failed capture must have a failure code")
        if self.state != "failed" and self.failure_code is not None:
            raise DiagnosticValidationError(
                "only failed captures may have a failure code"
            )
        _require_count(self.entry_count, "entry_count")
        _require_count(self.total_bytes, "total_bytes")
        if (
            self.encrypted_payload is not None
            and type(self.encrypted_payload) is not bytes
        ):
            raise TypeError("encrypted capture payload must be bytes")
        if (
            self.state
            in {
                "uploaded",
                "preserved",
                "failed",
                "cancelled",
                "expired",
                "deleted",
            }
            and self.encrypted_payload is not None
        ):
            raise DiagnosticValidationError(
                "terminal capture cannot retain encrypted payload"
            )
        if self.entry_count == 0 and self.total_bytes != 0:
            raise DiagnosticValidationError(
                "capture byte count cannot exist without entries"
            )

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

    def __post_init__(self) -> None:
        _require_opaque_id(self.capture_id, "capture_id", prefix="capture-")
        _require_choice(self.action, "capture audit action", _CAPTURE_ACTIONS)
        _require_timestamp(self.occurred_at, "audit occurred_at")
        _require_choice(
            self.outcome,
            "capture audit outcome",
            _CAPTURE_AUDIT_OUTCOMES,
        )
        if self.outcome not in _CAPTURE_AUDIT_ALLOWED_OUTCOMES[self.action]:
            raise DiagnosticValidationError(
                "capture audit outcome is invalid for its action"
            )


@dataclass(frozen=True, slots=True)
class IncidentBundle:
    """Private sealed evidence handed to the separately injected upload port."""

    capture_id: str
    scope: CaptureScope
    encrypted_payload: bytes = field(repr=False)
    retention_deadline: float

    def __post_init__(self) -> None:
        _require_opaque_id(self.capture_id, "capture_id", prefix="capture-")
        if not isinstance(self.scope, CaptureScope):
            raise DiagnosticValidationError("capture scope is invalid")
        if type(self.encrypted_payload) is not bytes or not self.encrypted_payload:
            raise DiagnosticValidationError("encrypted bundle payload is invalid")
        _require_timestamp(self.retention_deadline, "retention_deadline")


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
        if (
            type(capture_ttl_seconds) is not int
            or not 1 <= capture_ttl_seconds <= MAX_SAFE_COUNT
        ):
            raise ValueError("capture TTL must be positive")
        if (
            type(max_capture_records) is not int
            or not 1 <= max_capture_records <= MAX_SAFE_COUNT
        ):
            raise ValueError("capture record bound must be positive")
        if (
            type(max_audit_records) is not int
            or not 1 <= max_audit_records <= MAX_SAFE_COUNT
        ):
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
        now = self._clock_now()
        capture = CaptureRecord(
            capture_id=f"capture-{uuid.uuid4().hex}",
            scope=scope,
            state="armed",
            created_at=now,
            expires_at=now + self._capture_ttl_seconds,
        )
        with self._lock:
            self._make_room_for_capture()
            self._commit_transition(capture, "arm", "accepted", now=now)
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
            entries = self._ring_buffer.entries(capture.scope, now=self._clock_now())
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
            self._commit_transition(capture, "preview", "accepted")
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
            approved = replace(
                capture,
                state="approved",
                entry_count=len(entries),
                total_bytes=sum(len(entry.evidence) for entry in entries),
            )
            self._evidence[capture.capture_id] = entries
            self._commit_transition(approved, "approve", "approved")
            if self._sealer is None:
                return self._fail(approved, "encryption_unavailable")
            encrypting = replace(approved, state="encrypting")
            self._commit_transition(encrypting, "approve", "accepted")

        try:
            encrypted = self._sealer.seal(
                tuple(entry.evidence for entry in entries),
                capture_id=capture_id,
                scope=approved.scope,
            )
            if type(encrypted) is not bytes or not encrypted:
                raise ValueError("sealer returned no encrypted payload")
        except Exception:  # noqa: BLE001 - sealing failure is typed
            with self._lock:
                try:
                    current = self._capture_for_external(capture_id, "encrypting")
                except CaptureStateError:
                    current = self._captures.get(capture_id)
                    if current is not None and current.state == "encrypting":
                        self._fail(current, "forbidden")
                    raise
                return self._fail(current, "encryption_failed")

        with self._lock:
            try:
                current = self._capture_for_external(capture_id, "encrypting")
            except CaptureStateError:
                current = self._captures.get(capture_id)
                if current is not None and current.state == "encrypting":
                    self._fail(current, "forbidden")
                raise
            now = self._clock_now()
            try:
                deadline = now + INCIDENT_RETENTION_SECONDS
                _require_timestamp(deadline, "incident retention deadline")
            except DiagnosticValidationError:
                return self._fail(current, "encryption_failed")
            uploading = replace(
                current,
                state="uploading",
                encrypted_payload=encrypted,
            )
            self._commit_transition(uploading, "approve", "accepted", now=now)
            try:
                if self._uploader is None:
                    return self._fail(uploading, "upload_unavailable")
                bundle = IncidentBundle(
                    capture_id=uploading.capture_id,
                    scope=uploading.scope,
                    encrypted_payload=encrypted,
                    retention_deadline=deadline,
                )
            except DiagnosticValidationError:
                return self._fail(uploading, "upload_failed")

        try:
            self._uploader.upload(bundle)
        except Exception:  # noqa: BLE001 - upload failure is typed
            with self._lock:
                try:
                    current = self._capture_for_external(capture_id, "uploading")
                except CaptureStateError:
                    current = self._captures.get(capture_id)
                    if current is not None and current.state == "uploading":
                        self._fail(current, "forbidden")
                    raise
                return self._fail(current, "upload_failed")

        cleanup_remote = False
        post_upload_error: CaptureStateError | None = None
        with self._lock:
            current = self._captures.get(capture_id)
            if current is None:
                post_upload_error = CaptureStateError("capture not found")
            else:
                try:
                    self._authorize_scope(current.scope)
                except CaptureStateError as error:
                    self._fail(current, "forbidden")
                    cleanup_remote = True
                    post_upload_error = error
                else:
                    now = self._clock_now()
                    if current.state != "uploading":
                        post_upload_error = CaptureStateError(
                            f"capture cannot be finalized from {current.state}"
                        )
                        cleanup_remote = True
                    elif self._capture_is_expired(current, now=now):
                        self._expire_capture(current, now=now)
                        cleanup_remote = True
                        post_upload_error = CaptureStateError("capture expired")
                    else:
                        uploaded = replace(
                            current,
                            state="uploaded",
                            retention_deadline=deadline,
                            encrypted_payload=None,
                        )
                        self._commit_transition(
                            uploaded, "approve", "uploaded", now=now
                        )
                        self._evidence.pop(capture_id, None)
                        return uploaded

        if cleanup_remote and self._bundle_lifecycle is not None:
            try:
                self._bundle_lifecycle.delete(capture_id)
            except Exception as error:  # noqa: BLE001 - cleanup is best effort
                del error
        if post_upload_error is not None:
            raise post_upload_error
        raise CaptureStateError("capture upload could not be finalized")

    def cancel(self, capture_id: str) -> CaptureRecord:
        with self._lock:
            capture = self._active_capture(capture_id)
            self._authorize_scope(capture.scope)
            if capture.state not in {"armed", "previewing", "failed"}:
                raise CaptureStateError(
                    f"capture cannot be cancelled from {capture.state}"
                )
            capture = replace(capture, state="cancelled", failure_code=None)
            self._evidence.pop(capture.capture_id, None)
            self._commit_transition(capture, "cancel", "cancelled")
            return capture

    def preserve(self, capture_id: str) -> CaptureRecord:
        with self._lock:
            capture = self._active_capture(capture_id)
            self._authorize_scope(capture.scope)
            if capture.state != "uploaded":
                raise CaptureStateError(
                    f"capture cannot be preserved from {capture.state}"
                )
        remote_preserved = False
        if self._bundle_lifecycle is not None:
            try:
                self._bundle_lifecycle.preserve(capture.capture_id)
            except Exception as error:
                with self._lock:
                    self._audit_event(capture, "preserve", "failed")
                raise CaptureStateError("remote preserve failed") from error
            remote_preserved = True
        try:
            with self._lock:
                current = self._capture_for_external(capture_id, "uploaded")
                preserved = replace(current, state="preserved", retention_deadline=None)
                return self._commit_transition(preserved, "preserve", "preserved")
        except Exception:
            if remote_preserved and self._bundle_lifecycle is not None:
                try:
                    self._bundle_lifecycle.delete(capture_id)
                except Exception as error:  # noqa: BLE001 - cleanup is best effort
                    with self._lock:
                        current = self._captures.get(capture_id)
                        if current is not None:
                            try:
                                self._audit_event(current, "delete", "failed")
                            except Exception as audit_error:  # noqa: BLE001
                                del audit_error
                    del error
            raise

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
                remote_delete = True
            else:
                remote_delete = False
        if remote_delete and self._bundle_lifecycle is not None:
            try:
                self._bundle_lifecycle.delete(capture.capture_id)
            except Exception as error:
                with self._lock:
                    self._audit_event(capture, "delete", "failed")
                raise CaptureStateError("remote delete failed") from error
        with self._lock:
            current = self._capture_for_external(capture_id, capture.state)
            deleted = replace(
                current,
                state="deleted",
                retention_deadline=None,
                failure_code=None,
                encrypted_payload=None,
            )
            record = self._new_audit_record(deleted, "delete", "deleted")
            self._remove_capture_and_audit(capture_id, record)
            self._captures.pop(capture_id, None)
            self._evidence.pop(capture_id, None)
            self._audit.append(record)
            self._trim_memory()
            return deleted

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
        remote_cleanup: list[str] = []
        with self._lock:
            now = self._clock_now()
            expired = 0
            for capture in tuple(self._captures.values()):
                if self._capture_is_expired(capture, now=now):
                    if capture.state in {"uploaded", "preserved"}:
                        remote_cleanup.append(capture.capture_id)
                    self._expire_capture(capture, now=now)
                    expired += 1
        for capture_id in remote_cleanup:
            if self._bundle_lifecycle is None:
                continue
            try:
                self._bundle_lifecycle.delete(capture_id)
            except Exception as error:  # noqa: BLE001 - expiry remains local
                with self._lock:
                    capture = self._captures.get(capture_id)
                    if capture is not None:
                        self._audit_event(capture, "delete", "failed")
                del error
        return expired

    def close(self) -> None:
        """Detach the runtime-owned expiry callback, if one was registered."""
        if (
            self._expiry_scheduler is not None
            and self._expiry_scheduler_handle is not None
        ):
            self._expiry_scheduler.unregister(self._expiry_scheduler_handle)
            self._expiry_scheduler_handle = None

    def _clock_now(self) -> float:
        now = self._clock()
        _require_timestamp(now, "capture clock")
        return float(now)

    def _active_capture(self, capture_id: str, *, expire: bool = True) -> CaptureRecord:
        _require_opaque_id(capture_id, "capture_id", prefix="capture-")
        capture = self._captures.get(capture_id)
        if capture is None:
            raise CaptureStateError("capture not found")
        if not expire:
            return capture
        now = self._clock_now()
        if self._capture_is_expired(capture, now=now):
            self._expire_capture(capture, now=now)
            raise CaptureStateError("capture expired")
        return capture

    @staticmethod
    def _capture_is_expired(capture: CaptureRecord, *, now: float) -> bool:
        capture_expired = (
            capture.state
            in {
                "armed",
                "previewing",
                "approved",
                "encrypting",
                "uploading",
                "failed",
            }
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
            failure_code=None,
            encrypted_payload=None,
        )
        self._evidence.pop(capture.capture_id, None)
        return self._commit_transition(expired, "expire", "expired", now=now)

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

    def _capture_for_external(
        self, capture_id: str, expected_state: str
    ) -> CaptureRecord:
        capture = self._active_capture(capture_id)
        self._authorize_scope(capture.scope)
        if capture.state != expected_state:
            raise CaptureStateError(f"capture cannot continue from {capture.state}")
        return capture

    def _select_evidence(
        self,
        capture_id: str,
        evidence_ids: Sequence[str],
    ) -> tuple[RingBufferEntry, ...]:
        if not isinstance(evidence_ids, Sequence) or isinstance(
            evidence_ids, (str, bytes)
        ):
            raise CaptureStateError("evidence selection must be a sequence of IDs")
        selected_ids = tuple(evidence_ids)
        if not selected_ids:
            raise CaptureStateError("at least one evidence ID must be selected")
        if any(type(evidence_id) is not str for evidence_id in selected_ids):
            raise CaptureStateError("evidence selection contains an invalid ID")
        for evidence_id in selected_ids:
            try:
                _require_opaque_id(evidence_id, "evidence_id", prefix="evidence-")
            except DiagnosticValidationError as error:
                raise CaptureStateError(
                    "evidence selection contains an invalid ID"
                ) from error
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
        self._evidence.pop(capture.capture_id, None)
        outcome = code if code in _CAPTURE_AUDIT_OUTCOMES else "failed"
        return self._commit_transition(capture, "approve", outcome)

    def _new_audit_record(
        self,
        capture: CaptureRecord,
        action: str,
        outcome: str,
        *,
        now: float | None = None,
    ) -> CaptureAuditRecord:
        return CaptureAuditRecord(
            capture_id=capture.capture_id,
            action=action,
            occurred_at=self._clock_now() if now is None else now,
            outcome=outcome,
        )

    def _commit_transition(
        self,
        capture: CaptureRecord,
        action: str,
        outcome: str,
        *,
        now: float | None = None,
    ) -> CaptureRecord:
        record = self._new_audit_record(capture, action, outcome, now=now)
        if self._capture_store is not None:
            atomic_commit = getattr(self._capture_store, "save_capture_and_audit", None)
            if callable(atomic_commit):
                atomic_commit(capture, record)
            else:
                self._capture_store.save_capture(capture)
                self._capture_store.append_audit(record)
        self._captures[capture.capture_id] = capture
        self._audit.append(record)
        self._trim_memory()
        return capture

    def _trim_memory(self) -> None:
        while len(self._captures) > self._max_capture_records:
            if not self._evict_oldest_terminal():
                raise CaptureStateError("capture record bound is full")

        if len(self._audit) > self._max_audit_records:
            del self._audit[: -self._max_audit_records]

    def _make_room_for_capture(self) -> None:
        while len(self._captures) >= self._max_capture_records:
            if not self._evict_oldest_terminal():
                raise CaptureStateError("capture record bound is full")

    def _evict_oldest_terminal(self) -> bool:
        terminal = [
            capture
            for capture in self._captures.values()
            if capture.state
            not in {"armed", "previewing", "approved", "encrypting", "uploading"}
        ]
        if not terminal:
            return False
        oldest = min(
            terminal, key=lambda capture: (capture.created_at, capture.capture_id)
        )
        if self._capture_store is not None:
            remove = getattr(self._capture_store, "remove_capture", None)
            if not callable(remove):
                raise DiagnosticStoreError("capture eviction is unavailable")
            remove(oldest.capture_id)
        self._captures.pop(oldest.capture_id, None)
        self._evidence.pop(oldest.capture_id, None)
        return True

    def _audit_event(
        self,
        capture: CaptureRecord,
        action: str,
        outcome: str,
        *,
        now: float | None = None,
    ) -> None:
        record = self._new_audit_record(capture, action, outcome, now=now)
        if self._capture_store is not None:
            self._capture_store.append_audit(record)
        self._audit.append(record)
        self._trim_memory()

    def _remove_capture_and_audit(
        self, capture_id: str, record: CaptureAuditRecord
    ) -> None:
        if self._capture_store is None:
            return
        atomic_remove = getattr(self._capture_store, "remove_capture_and_audit", None)
        if callable(atomic_remove):
            atomic_remove(capture_id, record)
            return
        remove = getattr(self._capture_store, "remove_capture", None)
        if not callable(remove):
            raise DiagnosticStoreError("capture deletion is unavailable")
        remove(capture_id)
        self._capture_store.append_audit(record)


def _require_token(value: object, name: str, *, max_length: int = 128) -> None:
    if type(value) is not str or not 1 <= len(value) <= max_length:
        raise DiagnosticValidationError(f"{name} must be a bounded token")
    if any(character in value for character in "\r\n"):
        raise DiagnosticValidationError(f"{name} must not contain line breaks")
    if any(not (character.isalnum() or character in "._:-+") for character in value):
        raise DiagnosticValidationError(f"{name} contains unsafe characters")


def _require_opaque_id(value: object, name: str, *, prefix: str) -> None:
    if type(value) is not str or not re.fullmatch(
        re.escape(prefix) + r"[0-9a-f]{32}", value
    ):
        raise DiagnosticValidationError(f"{name} must be an opaque identifier")


def _require_choice(value: object, name: str, allowed: frozenset[str]) -> None:
    if type(value) is not str or value not in allowed:
        raise DiagnosticValidationError(f"{name} is not safe")


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
    if (
        type(value) is not str
        or len(value) > MAX_SERVICE_VERSION_LENGTH
        or not re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", value)
    ):
        raise DiagnosticValidationError("service version is not safe")


def _event_expired(event: DiagnosticEvent, *, before: float) -> bool:
    deadline = event.retention_deadline
    if deadline is None:
        try:
            deadline = event.occurred_at + _RETENTION_SECONDS[event.retention_class]
        except (OverflowError, TypeError, ValueError) as error:
            raise DiagnosticValidationError(
                "event retention deadline cannot be represented"
            ) from error
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
    if type(value) not in (int, float) or isinstance(value, bool):
        return False
    try:
        numeric_value = float(value)
    except OverflowError, TypeError, ValueError:
        return False
    return isfinite(numeric_value) and numeric_value > 0


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
    if type(value) is not int or not 0 <= value <= MAX_SAFE_COUNT:
        raise DiagnosticValidationError(f"{name} must be a non-negative integer")
