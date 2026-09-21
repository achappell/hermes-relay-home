"""Framework-independent HTTP contract adapter."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from threading import RLock

from hermes_home.auth.credentials import PersistentCredentialAuthenticator
from hermes_home.auth.static import StaticCredentialAuthenticator
from hermes_home.domain.arbitration import ArbitrationEngine, ClaimSubmission
from hermes_home.domain.configuration import ConfigurationValidationError
from hermes_home.domain.conversations import (
    ConversationClaimConflict,
    ConversationClaimStore,
)
from hermes_home.domain.credentials import (
    CredentialMaterial,
    CredentialScope,
    CredentialService,
    CredentialStateError,
    CredentialValidationError,
)
from hermes_home.domain.health import (
    HEALTH_CHECK_TIMEOUT_SECONDS,
    HEALTH_VIEW_CAPABILITY,
    HealthDeliveryState,
    HealthProbeResult,
    HealthResult,
    HealthStage,
    HealthValidationError,
    bounded_health_body,
    run_health_check,
)
from hermes_home.domain.watch import (
    WATCH_VIEW_CAPABILITY,
    WatchSnapshot,
    WatchSnapshotProvider,
    activity_summary,
    serialize_watch_route,
)
from hermes_home.observability.diagnostics import (
    DiagnosticEvent,
    DiagnosticsRecorder,
    DiagnosticStoreError,
    DiagnosticValidationError,
    InMemoryDiagnosticsStore,
)
from hermes_home.observability.metrics import MetricsRegistry
from hermes_home.storage.credentials import CredentialStoreError
from hermes_home.storage.sqlite import (
    ConfigurationMigrationRequired,
    ConfigurationStoreError,
    RevisionConflict,
)

MAX_REQUEST_BODY_BYTES = 1_048_576


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    status: int
    body: dict[str, object] | str = field(repr=False)
    content_type: str = "application/json; charset=utf-8"


class HomeApplication:
    """Translate the v1 HTTP contract into domain and storage operations."""

    def __init__(
        self,
        *,
        configuration_store,
        arbitration_engine: ArbitrationEngine,
        admin_token: str,
        device_credentials: Mapping[str, str],
        credential_service: CredentialService | None = None,
        conversation_claim_store: ConversationClaimStore | None = None,
        static_device_scopes: Mapping[str, CredentialScope] | None = None,
        watch_snapshot_provider: WatchSnapshotProvider | None = None,
        health_probe_provider=None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        metrics: MetricsRegistry | None = None,
        diagnostics: DiagnosticsRecorder | None = None,
    ) -> None:
        self._configuration_store = configuration_store
        self._arbitration_engine = arbitration_engine
        self._credential_service = credential_service
        self._conversation_claim_store = conversation_claim_store
        self._configuration_publish_lock = RLock()
        self._pending_configuration_cleanup: dict[
            int, tuple[Mapping[str, object] | None, Mapping[str, object]]
        ] = {}
        self._static_device_scopes = dict(static_device_scopes or {})
        self._watch_snapshot_provider = watch_snapshot_provider
        self._health_probe_provider = health_probe_provider
        if credential_service is None:
            self._authenticator = StaticCredentialAuthenticator(
                admin_token=admin_token,
                device_credentials=device_credentials,
            )
        else:
            self._authenticator = PersistentCredentialAuthenticator(
                admin_token=admin_token,
                service=credential_service,
            )
        self._clock = clock
        self._sleeper = sleeper
        self._metrics = metrics or MetricsRegistry()
        self._diagnostics = diagnostics or DiagnosticsRecorder(
            store=InMemoryDiagnosticsStore(),
            metrics=self._metrics,
        )
        try:
            snapshot = self._configuration_store.read()
        except ConfigurationMigrationRequired:
            pass
        except OSError, RuntimeError, TypeError, ValueError:
            pass
        else:
            self._set_revision(snapshot)

    @property
    def device_authenticator(self):
        """Return the authenticator shared by HTTP and bridge callers."""

        return self._authenticator

    def handle(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | str,
    ) -> HTTPResponse:
        request_path = path.split("?", 1)[0]
        started = time.perf_counter()
        correlation_id = self._diagnostics.new_correlation_id()
        try:
            response = self._dispatch(
                method,
                request_path,
                headers,
                body,
                correlation_id=correlation_id,
            )
        except OSError, RuntimeError, TypeError, ValueError:
            self._record_http(method, request_path, 500, started)
            self._record_diagnostic(
                correlation_id,
                path=request_path,
                status=500,
                started=started,
            )
            raise
        self._record_http(method, request_path, response.status, started)
        self._record_outcome(method, request_path, response)
        self._record_diagnostic(
            correlation_id,
            path=request_path,
            status=response.status,
            started=started,
            response=response,
        )
        return response

    def _dispatch(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | str,
        *,
        correlation_id: str | None = None,
    ) -> HTTPResponse:
        if path == "/api/v1/configuration":
            if method == "GET":
                return self._get_configuration(headers)
            if method == "PUT":
                return self._put_configuration(headers, body)
        elif path == "/api/v1/wake-claims" and method == "POST":
            return self._post_wake_claim(headers, body)
        elif path == "/api/v1/enrollment/offers" and method == "POST":
            return self._post_enrollment_offer(headers, body)
        elif path == "/api/v1/enrollment/requests" and method == "GET":
            return self._get_enrollment_requests(headers)
        elif path == "/api/v1/enrollment/requests" and method == "POST":
            return self._post_enrollment_request(headers, body)
        elif path == "/metrics" and method == "GET":
            return self._get_metrics(headers)
        elif path == "/api/v1/diagnostics/status" and method == "GET":
            return self._get_diagnostics_status(headers)
        else:
            parts = path.split("/")
            if (
                len(parts) == 6
                and parts[1:5] == ["api", "v1", "diagnostics", "timeline"]
                and method == "GET"
            ):
                return self._get_diagnostics_timeline(headers, parts[5])
            if len(parts) == 7 and parts[1:5] == [
                "api",
                "v1",
                "enrollment",
                "requests",
            ]:
                request_id = parts[5]
                action = parts[6]
                if method == "POST" and action == "approve":
                    return self._approve_enrollment_request(headers, body, request_id)
                if method == "POST" and action == "reject":
                    return self._reject_enrollment_request(headers, body, request_id)
                if method == "POST" and action == "consume":
                    return self._consume_enrollment_request(headers, body, request_id)
            if len(parts) == 6 and parts[1:4] == ["api", "v1", "devices"]:
                device_id = parts[4]
                if method == "POST" and parts[5] == "revoke":
                    return self._revoke_device(headers, body, device_id)
                if method == "GET" and parts[5] == "configuration":
                    return self._get_device_configuration(headers, device_id)
                if method == "GET" and parts[5] == "watch":
                    return self._get_watch(headers, device_id)
                if method == "GET" and parts[5] == "health":
                    return self._get_health(
                        headers,
                        device_id,
                        correlation_id=correlation_id
                        or self._diagnostics.new_correlation_id(),
                    )
            if (
                len(parts) == 7
                and parts[1:4] == ["api", "v1", "devices"]
                and parts[5] == "credentials"
            ):
                device_id = parts[4]
                if method == "POST" and parts[6] == "renew":
                    return self._renew_device(headers, body, device_id)
                if method == "POST" and parts[6] == "rotate":
                    return self._rotate_device(headers, body, device_id)
        return _error(404, "not_found")

    @staticmethod
    def _json_request(
        headers: Mapping[str, str],
        body: bytes | str,
        expected_fields: set[str],
        *,
        optional_fields: set[str] | frozenset[str] = frozenset(),
    ) -> dict[str, object]:
        if not _is_json_content_type(headers):
            raise ValueError("JSON content type is required")
        request = _json_object(body)
        actual_fields = set(request)
        if actual_fields != expected_fields and actual_fields != expected_fields - set(
            optional_fields
        ):
            raise ValueError("invalid request fields")
        _require_schema(request)
        return request

    def _durable_device_context(
        self,
        headers: Mapping[str, str],
        *,
        allow_replaced: bool = False,
    ):
        if not isinstance(self._authenticator, PersistentCredentialAuthenticator):
            return None
        return self._authenticator.authenticate_device_context(
            headers,
            allow_replaced=allow_replaced,
        )

    def _post_enrollment_offer(
        self,
        headers: Mapping[str, str],
        body: bytes | str,
    ) -> HTTPResponse:
        if not self._authenticator.authenticate_admin(headers):
            return _error(401, "unauthorized")
        if self._credential_service is None:
            return _error(503, "service_unavailable")
        if not _is_json_content_type(headers):
            return _error(400, "invalid_request")
        try:
            request = _json_object(body)
            if set(request) not in ({"schema"}, {"schema", "expires_in_seconds"}):
                raise ValueError("invalid offer fields")
            _require_schema(request)
            expires_in_seconds = request.get("expires_in_seconds", 300)
            offer = self._credential_service.create_offer(
                expires_in_seconds=expires_in_seconds
            )
        except CredentialValidationError, TypeError, ValueError, json.JSONDecodeError:
            return _error(400, "invalid_request")
        except CredentialStoreError:
            return _error(503, "service_unavailable")
        return HTTPResponse(
            200,
            {
                "schema": 1,
                "offer_id": offer.offer_id,
                "enrollment_code": offer.enrollment_code,
                "expires_at": offer.expires_at,
            },
        )

    def _post_enrollment_request(
        self,
        headers: Mapping[str, str],
        body: bytes | str,
    ) -> HTTPResponse:
        if self._credential_service is None:
            return _error(503, "service_unavailable")
        if not _is_json_content_type(headers):
            return _error(400, "invalid_request")
        try:
            expected = {
                "schema",
                "enrollment_code",
                "endpoint_id",
                "label",
                "type",
                "requested_rooms",
                "requested_capabilities",
                "requested_profile_mappings",
                "secure_storage",
            }
            request = self._json_request(
                headers,
                body,
                expected,
                optional_fields={"requested_profile_mappings"},
            )
            enrollment = self._credential_service.submit_request(
                enrollment_code=request["enrollment_code"],
                endpoint_id=request["endpoint_id"],
                label=request["label"],
                endpoint_type=request["type"],
                requested_rooms=request["requested_rooms"],
                requested_capabilities=request["requested_capabilities"],
                requested_profile_mappings=request.get(
                    "requested_profile_mappings", ()
                ),
                secure_storage=request["secure_storage"],
            )
        except CredentialStateError as error:
            return _credential_error(error)
        except CredentialValidationError, TypeError, ValueError, json.JSONDecodeError:
            return _error(400, "invalid_request")
        except CredentialStoreError:
            return _error(503, "service_unavailable")
        return HTTPResponse(
            200,
            {
                "schema": 1,
                "request_id": enrollment.request_id,
                "confirmation_code": enrollment.confirmation_code,
                "expires_at": enrollment.expires_at,
            },
        )

    def _get_enrollment_requests(
        self,
        headers: Mapping[str, str],
    ) -> HTTPResponse:
        if not self._authenticator.authenticate_admin(headers):
            return _error(401, "unauthorized")
        if self._credential_service is None:
            return _error(503, "service_unavailable")
        try:
            requests = self._credential_service.list_requests()
            payload = [request.to_public() for request in requests]
        except CredentialStoreError, KeyError, TypeError, ValueError:
            return _error(503, "service_unavailable")
        return HTTPResponse(
            200,
            {"schema": 1, "requests": payload},
        )

    def _approve_enrollment_request(
        self,
        headers: Mapping[str, str],
        body: bytes | str,
        request_id: str,
    ) -> HTTPResponse:
        if not self._authenticator.authenticate_admin(headers):
            return _error(401, "unauthorized")
        if self._credential_service is None:
            return _error(503, "service_unavailable")
        try:
            request = self._json_request(headers, body, {"schema", "scope"})
            scope_data = request["scope"]
            if not isinstance(scope_data, Mapping) or set(scope_data) != {
                "rooms",
                "capabilities",
                "wake_mapping_grant",
            }:
                raise ValueError("invalid scope")
            snapshot = self._configuration_store.read()
            mapping_ids = _approved_wake_mapping_ids(
                snapshot, scope_data["wake_mapping_grant"]
            )
            scope = CredentialScope.from_values(
                rooms=scope_data["rooms"],
                capabilities=scope_data["capabilities"],
                wake_mappings=mapping_ids,
            )
            configured_rooms = [room["id"] for room in snapshot["rooms"]]
            configured_wake_mappings = _available_wake_mapping_ids(snapshot)
            enrollment = self._credential_service.approve_request(
                request_id,
                scope,
                configured_rooms=configured_rooms,
                configured_wake_mappings=configured_wake_mappings,
            )
        except CredentialStateError as error:
            return _credential_error(error)
        except CredentialStoreError:
            return _error(503, "service_unavailable")
        except ConfigurationMigrationRequired as error:
            return HTTPResponse(
                409,
                {
                    "schema": 1,
                    "error": {
                        "code": "configuration_migration_required",
                        "current_revision": error.current_revision,
                    },
                },
            )
        except ConfigurationStoreError:
            return _error(503, "service_unavailable")
        except OSError, RuntimeError, KeyError:
            return _error(503, "service_unavailable")
        except (
            ConfigurationValidationError,
            CredentialValidationError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return _error(400, "invalid_request")
        return HTTPResponse(200, {"schema": 1, "request": enrollment.to_public()})

    def _get_device_configuration(
        self,
        headers: Mapping[str, str],
        device_id: str,
    ) -> HTTPResponse:
        try:
            context = self._durable_device_context(headers)
        except OSError, RuntimeError, TypeError, ValueError:
            return _error(503, "service_unavailable")
        if context is None or context.device_id != device_id:
            return _error(401, "unauthorized")
        if "wake_claim" not in context.scope.capabilities:
            return _error(403, "forbidden")
        try:
            snapshot = self._configuration_store.read()
        except ConfigurationMigrationRequired as error:
            return HTTPResponse(
                409,
                {
                    "schema": 1,
                    "error": {
                        "code": "configuration_migration_required",
                        "current_revision": error.current_revision,
                    },
                },
            )
        except OSError, RuntimeError, TypeError, ValueError:
            return _error(503, "service_unavailable")

        authorized_ids = set(context.scope.wake_mappings)
        profile_availability = {
            profile["id"]: profile["available"] for profile in snapshot["profiles"]
        }
        mappings = [
            {"id": mapping["id"], "phrase": mapping["phrase"]}
            for mapping in snapshot["wake_mappings"]
            if mapping["id"] in authorized_ids
            and mapping["active"]
            and profile_availability.get(mapping["profile_id"], False)
        ]
        return HTTPResponse(
            200,
            {
                "schema": 1,
                "snapshot": {
                    "revision": snapshot["revision"],
                    "wake_mappings": mappings,
                },
            },
        )

    def _get_watch(
        self,
        headers: Mapping[str, str],
        target_device_id: str,
    ) -> HTTPResponse:
        try:
            watcher_device_id, _generation, scope = self._watch_context(headers)
        except OSError, RuntimeError, TypeError, ValueError:
            return _error(503, "service_unavailable")
        if watcher_device_id is None:
            return _error(401, "unauthorized")
        if WATCH_VIEW_CAPABILITY not in scope.capabilities:
            return _error(403, "forbidden")
        try:
            configuration = self._configuration_store.read()
        except ConfigurationMigrationRequired as error:
            return HTTPResponse(
                409,
                {
                    "schema": 1,
                    "error": {
                        "code": "configuration_migration_required",
                        "current_revision": error.current_revision,
                    },
                },
            )
        except OSError, RuntimeError, TypeError, ValueError:
            return _error(503, "service_unavailable")

        target = next(
            (
                device
                for device in configuration["devices"]
                if device["id"] == target_device_id
            ),
            None,
        )
        if target is None or target["room_id"] not in scope.rooms:
            return _error(404, "not_found")

        provider = self._watch_snapshot_provider or self._conversation_claim_store
        read_snapshot = getattr(provider, "watch_snapshot", None)
        if not callable(read_snapshot):
            return _watch_unavailable("observation_unavailable", target)
        try:
            current = read_snapshot(
                target_device_id,
                configuration_revision=configuration["revision"],
            )
        except OSError, RuntimeError, TypeError, ValueError:
            return _watch_unavailable("observation_unavailable", target)
        if current is None:
            return _watch_unavailable("no_current_state", target)
        if not isinstance(current, WatchSnapshot):
            return _watch_unavailable("observation_unavailable", target)
        if (
            current.device_id != target_device_id
            or current.configuration_revision != configuration["revision"]
        ):
            return _watch_unavailable("stale_state", target)

        profiles = {profile["id"]: profile for profile in configuration["profiles"]}
        profile = profiles.get(current.profile_id)
        if profile is None or profile["available"] is not True:
            return _watch_unavailable("no_current_state", target)
        try:
            route = serialize_watch_route(current.route)
        except TypeError, ValueError:
            return _watch_unavailable("observation_unavailable", target)
        summary = activity_summary(
            current.activity,
            session_present=current.session_present,
        )
        return HTTPResponse(
            200,
            {
                "schema": 1,
                "watch": {
                    "status": "available",
                    "endpoint": {
                        "id": target["id"],
                        "name": target["name"],
                    },
                    "profile_label": profile["name"],
                    "route": route,
                    "health": current.health,
                    "task": {
                        "state": current.activity,
                        "summary": summary,
                        "session_present": current.session_present,
                    },
                    "preview": {
                        "kind": "safe_state",
                        "summary": summary,
                    },
                },
            },
        )

    def _get_health(
        self,
        headers: Mapping[str, str],
        target_device_id: str,
        *,
        correlation_id: str,
    ) -> HTTPResponse:
        try:
            _caller_device_id, _generation, scope = self._watch_context(headers)
        except OSError, RuntimeError, TypeError, ValueError:
            return _error(503, "service_unavailable")
        if _caller_device_id is None:
            return _error(401, "unauthorized")
        if HEALTH_VIEW_CAPABILITY not in scope.capabilities:
            return _error(403, "forbidden")
        try:
            configuration = self._configuration_store.read()
        except ConfigurationMigrationRequired as error:
            return HTTPResponse(
                409,
                {
                    "schema": 1,
                    "error": {
                        "code": "configuration_migration_required",
                        "current_revision": error.current_revision,
                    },
                },
            )
        except OSError, RuntimeError, TypeError, ValueError:
            return _error(503, "service_unavailable")

        target = next(
            (
                device
                for device in configuration["devices"]
                if device["id"] == target_device_id
            ),
            None,
        )
        if target is None or target["room_id"] not in scope.rooms:
            return _error(404, "not_found")

        provider = self._health_probe_provider
        delivery_provider = provider
        if not callable(getattr(delivery_provider, "delivery_state", None)):
            delivery_provider = self._conversation_claim_store
        try:
            result = run_health_check(
                correlation_id=correlation_id,
                device_id=target_device_id,
                room_id=target["room_id"],
                authorization=HealthProbeResult.verified(),
                provider=provider,
                delivery_provider=delivery_provider,
                timeout=HEALTH_CHECK_TIMEOUT_SECONDS,
                clock=self._clock,
            )
            current_configuration = self._configuration_store.read()
            current_target = next(
                (
                    device
                    for device in current_configuration["devices"]
                    if device["id"] == target_device_id
                ),
                None,
            )
            if current_target is None or current_target["room_id"] != target["room_id"]:
                result = _stale_health_failure(correlation_id)
            body = bounded_health_body(result)
        except HealthValidationError:
            result = _safe_health_failure(correlation_id)
            body = bounded_health_body(result)
        except ConfigurationMigrationRequired as error:
            return HTTPResponse(
                409,
                {
                    "schema": 1,
                    "error": {
                        "code": "configuration_migration_required",
                        "current_revision": error.current_revision,
                    },
                },
            )
        except OSError, RuntimeError, TypeError, ValueError:
            return _error(503, "service_unavailable")
        return HTTPResponse(200, body)

    def _watch_context(
        self,
        headers: Mapping[str, str],
    ) -> tuple[str | None, int | None, CredentialScope]:
        if self._credential_service is None:
            device_id = self._authenticator.authenticate_device(headers)
            if device_id is None:
                return (
                    None,
                    None,
                    CredentialScope.from_values(rooms=(), capabilities=()),
                )
            return (
                device_id,
                None,
                self._static_device_scopes.get(
                    device_id,
                    CredentialScope.from_values(rooms=(), capabilities=()),
                ),
            )
        context = self._durable_device_context(headers)
        if context is None:
            return None, None, CredentialScope.from_values(rooms=(), capabilities=())
        return context.device_id, context.generation, context.scope

    def _reject_enrollment_request(
        self,
        headers: Mapping[str, str],
        body: bytes | str,
        request_id: str,
    ) -> HTTPResponse:
        if not self._authenticator.authenticate_admin(headers):
            return _error(401, "unauthorized")
        if self._credential_service is None:
            return _error(503, "service_unavailable")
        try:
            request = self._json_request(
                headers,
                body,
                {"schema", "reason"},
                optional_fields={"reason"},
            )
            reason = _optional_reason(request)
            enrollment = self._credential_service.reject_request(
                request_id, reason=reason
            )
        except CredentialStateError as error:
            return _credential_error(error)
        except CredentialValidationError, TypeError, ValueError, json.JSONDecodeError:
            return _error(400, "invalid_request")
        except CredentialStoreError:
            return _error(503, "service_unavailable")
        return HTTPResponse(200, {"schema": 1, "request": enrollment.to_public()})

    def _consume_enrollment_request(
        self,
        headers: Mapping[str, str],
        body: bytes | str,
        request_id: str,
    ) -> HTTPResponse:
        if self._credential_service is None:
            return _error(503, "service_unavailable")
        try:
            request = self._json_request(
                headers, body, {"schema", "enrollment_code", "secure_storage"}
            )
            material = self._credential_service.consume_request(
                request_id,
                enrollment_code=request["enrollment_code"],
                secure_storage=request["secure_storage"],
            )
        except CredentialStateError as error:
            return _credential_error(error)
        except CredentialValidationError, TypeError, ValueError, json.JSONDecodeError:
            return _error(400, "invalid_request")
        except CredentialStoreError:
            return _error(503, "service_unavailable")
        return HTTPResponse(200, _material_payload(material))

    def _renew_device(
        self,
        headers: Mapping[str, str],
        body: bytes | str,
        device_id: str,
    ) -> HTTPResponse:
        if self._credential_service is None:
            return _error(503, "service_unavailable")
        try:
            context = self._durable_device_context(headers, allow_replaced=True)
        except OSError, RuntimeError, TypeError, ValueError:
            return _error(503, "service_unavailable")
        if context is None:
            return _error(401, "unauthorized")
        try:
            request = self._json_request(
                headers, body, {"schema", "request_id", "generation"}
            )
            material = self._credential_service.renew(
                device_id=device_id,
                credential=_device_credential(headers),
                request_id=request["request_id"],
                expected_generation=request["generation"],
            )
        except CredentialStateError as error:
            return _credential_error(error)
        except CredentialValidationError, TypeError, ValueError, json.JSONDecodeError:
            return _error(400, "invalid_request")
        except CredentialStoreError:
            return _error(503, "service_unavailable")
        try:
            self._close_device_claims(
                device_id,
                current_generation=material.generation,
                reason="endpoint_revoked",
            )
        except OSError, RuntimeError:
            return _error(503, "service_unavailable")
        return HTTPResponse(200, _material_payload(material))

    def _rotate_device(
        self,
        headers: Mapping[str, str],
        body: bytes | str,
        device_id: str,
    ) -> HTTPResponse:
        if not self._authenticator.authenticate_admin(headers):
            return _error(401, "unauthorized")
        if self._credential_service is None:
            return _error(503, "service_unavailable")
        try:
            request = self._json_request(
                headers, body, {"schema", "request_id", "generation"}
            )
            material = self._credential_service.rotate(
                device_id=device_id,
                request_id=request["request_id"],
                expected_generation=request["generation"],
            )
        except CredentialStateError as error:
            return _credential_error(error)
        except CredentialValidationError, TypeError, ValueError, json.JSONDecodeError:
            return _error(400, "invalid_request")
        except CredentialStoreError:
            return _error(503, "service_unavailable")
        try:
            self._close_device_claims(
                device_id,
                current_generation=material.generation,
                reason="endpoint_revoked",
            )
        except OSError, RuntimeError:
            return _error(503, "service_unavailable")
        return HTTPResponse(200, _material_payload(material))

    def _revoke_device(
        self,
        headers: Mapping[str, str],
        body: bytes | str,
        device_id: str,
    ) -> HTTPResponse:
        if not self._authenticator.authenticate_admin(headers):
            return _error(401, "unauthorized")
        if self._credential_service is None:
            return _error(503, "service_unavailable")
        try:
            request = self._json_request(
                headers,
                body,
                {"schema", "reason"},
                optional_fields={"reason"},
            )
            event = self._credential_service.revoke(
                device_id, reason=_optional_reason(request)
            )
        except CredentialStateError as error:
            return _credential_error(error)
        except CredentialValidationError, TypeError, ValueError, json.JSONDecodeError:
            return _error(400, "invalid_request")
        except CredentialStoreError:
            return _error(503, "service_unavailable")
        try:
            self._close_device_claims(
                device_id,
                current_generation=None,
                reason="endpoint_revoked",
            )
        except OSError, RuntimeError:
            return _error(503, "service_unavailable")
        return HTTPResponse(
            200,
            {
                "schema": 1,
                "device_id": event.device_id,
                "generation": event.generation,
                "status": "revoked",
            },
        )

    def _get_configuration(self, headers: Mapping[str, str]) -> HTTPResponse:
        if not self._authenticator.authenticate_admin(headers):
            return _error(401, "unauthorized")
        try:
            snapshot = self._configuration_store.read()
        except ConfigurationMigrationRequired as error:
            return HTTPResponse(
                409,
                {
                    "schema": 1,
                    "error": {
                        "code": "configuration_migration_required",
                        "current_revision": error.current_revision,
                    },
                },
            )
        except OSError, RuntimeError, TypeError, ValueError:
            return _error(503, "service_unavailable")
        return HTTPResponse(200, {"schema": 1, "snapshot": snapshot})

    def _get_metrics(self, headers: Mapping[str, str]) -> HTTPResponse:
        if not self._authenticator.authenticate_admin(headers):
            return _error(401, "unauthorized")
        return HTTPResponse(
            200,
            self._metrics.render(),
            content_type="text/plain; version=0.0.4; charset=utf-8",
        )

    def _get_diagnostics_status(
        self,
        headers: Mapping[str, str],
    ) -> HTTPResponse:
        if not (
            self._authenticator.authenticate_admin(headers)
            or self._authenticator.authenticate_device(headers) is not None
        ):
            return _error(401, "unauthorized")
        try:
            status = self._diagnostics.status()
        except DiagnosticStoreError:
            return _error(503, "service_unavailable")
        return HTTPResponse(200, status.to_dict())

    def _get_diagnostics_timeline(
        self,
        headers: Mapping[str, str],
        correlation_id: str,
    ) -> HTTPResponse:
        if not self._authenticator.authenticate_admin(headers):
            return _error(401, "unauthorized")
        try:
            events = self._diagnostics.timeline(correlation_id)
        except DiagnosticStoreError:
            return _error(503, "service_unavailable")
        except DiagnosticValidationError:
            return _error(400, "invalid_request")
        return HTTPResponse(
            200,
            {
                "schema": 1,
                "correlation_id": correlation_id,
                "events": [event.to_dict() for event in events],
            },
        )

    def _put_configuration(
        self,
        headers: Mapping[str, str],
        body: bytes | str,
    ) -> HTTPResponse:
        if not self._authenticator.authenticate_admin(headers):
            return _error(401, "unauthorized")
        with self._configuration_publish_lock:
            try:
                self._retry_pending_configuration_cleanups()
            except OSError, RuntimeError:
                return _error(503, "service_unavailable")
            return self._put_configuration_authenticated(headers, body)

    def _put_configuration_authenticated(
        self,
        headers: Mapping[str, str],
        body: bytes | str,
    ) -> HTTPResponse:
        if not _is_json_content_type(headers):
            return _error(400, "invalid_request")
        try:
            request = _json_object(body)
            if set(request) != {"schema", "expected_revision", "snapshot"}:
                raise ValueError("invalid configuration request fields")
            if type(request["schema"]) is not int or request["schema"] != 1:
                raise ValueError("unsupported schema")
            expected_revision = request["expected_revision"]
            if type(expected_revision) is not int or expected_revision < 0:
                raise ValueError("invalid expected revision")
            snapshot = request["snapshot"]
            if not isinstance(snapshot, Mapping):
                raise TypeError("snapshot must be an object")
            try:
                previous = self._configuration_store.read()
            except (
                ConfigurationMigrationRequired,
                ConfigurationStoreError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ):
                previous = None
            published = self._configuration_store.replace(
                expected_revision=expected_revision,
                candidate=snapshot,
            )
        except RevisionConflict as conflict:
            return HTTPResponse(
                409,
                {
                    "schema": 1,
                    "error": {
                        "code": "revision_conflict",
                        "current_revision": conflict.current_revision,
                    },
                },
            )
        except (
            ConfigurationValidationError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return _error(400, "invalid_request")
        except OSError, RuntimeError:
            return _error(503, "service_unavailable")
        self._pending_configuration_cleanup[published["revision"]] = (
            previous,
            published,
        )
        try:
            self._retry_pending_configuration_cleanups()
        except OSError, RuntimeError:
            return _error(503, "service_unavailable")
        return HTTPResponse(200, {"schema": 1, "snapshot": published})

    def _retry_pending_configuration_cleanups(self) -> None:
        """Retry post-commit revocation cleanup before later publishes."""

        for revision in sorted(self._pending_configuration_cleanup):
            previous, published = self._pending_configuration_cleanup[revision]
            self._close_revoked_configuration_claims(previous, published)
            self._pending_configuration_cleanup.pop(revision, None)

    def _close_device_claims(
        self,
        device_id: str,
        *,
        current_generation: int | None,
        reason: str,
    ) -> None:
        if self._conversation_claim_store is not None:
            self._conversation_claim_store.close_device_claims(
                device_id,
                current_generation=current_generation,
                reason=reason,
            )

    def _discard_new_claim(self, handle: str, device_id: str) -> bool:
        store = self._conversation_claim_store
        if store is None:
            return False
        try:
            return store.close_claim(
                handle,
                device_id,
                reason="endpoint_revoked",
            )
        except OSError, RuntimeError, TypeError, ValueError:
            return False

    def _close_revoked_configuration_claims(
        self,
        previous: Mapping[str, object] | None,
        published: Mapping[str, object],
    ) -> None:
        store = self._conversation_claim_store
        if store is None:
            return
        if previous is None:
            store.close_all_claims(reason="configuration_unverified")
            return

        current_profiles = {profile["id"]: profile for profile in published["profiles"]}
        revoked_profiles = {
            profile["id"]
            for profile in previous["profiles"]
            if profile["available"]
            and (
                profile["id"] not in current_profiles
                or not current_profiles[profile["id"]]["available"]
            )
        }
        current_mappings = {
            mapping["id"]: mapping for mapping in published["wake_mappings"]
        }
        revoked_mappings = {
            mapping["id"]
            for mapping in previous["wake_mappings"]
            if mapping["active"]
            and mapping["id"] in current_mappings
            and not current_mappings[mapping["id"]]["active"]
        }
        store.close_profile_claims(
            revoked_profiles,
            reason="profile_revoked",
        )
        store.close_mapping_claims(
            revoked_mappings,
            reason="mapping_revoked",
        )

    def _post_wake_claim(
        self,
        headers: Mapping[str, str],
        body: bytes | str,
    ) -> HTTPResponse:
        try:
            durable_context = self._durable_device_context(headers)
        except OSError, RuntimeError, TypeError, ValueError:
            return _error(503, "service_unavailable")
        if self._credential_service is None:
            authenticated_device_id = self._authenticator.authenticate_device(headers)
            if authenticated_device_id is None:
                return _error(401, "unauthorized")
            scope = self._static_device_scopes.get(
                authenticated_device_id,
                CredentialScope.from_values(rooms=(), capabilities=()),
            )
        else:
            if durable_context is None:
                return _error(401, "unauthorized")
            authenticated_device_id = durable_context.device_id
            scope = durable_context.scope
        if not _is_json_content_type(headers):
            return _error(400, "invalid_request")
        try:
            claim = _json_object(body)
        except TypeError, ValueError, json.JSONDecodeError:
            return _error(400, "invalid_request")

        submission = self._arbitration_engine.submit(
            claim,
            authenticated_device_id=authenticated_device_id,
            authorized_rooms=scope.rooms,
            authorized_wake_claim="wake_claim" in scope.capabilities,
            authorized_wake_mappings=scope.wake_mappings,
        )
        if not submission.accepted:
            return _claim_submission_error(submission)
        if submission.deadline is None:
            return _error(503, "service_unavailable")
        self._sleeper(max(0.0, submission.deadline - self._clock()))
        if durable_context is not None:
            try:
                refreshed = self._durable_device_context(headers)
            except OSError, RuntimeError, TypeError, ValueError:
                self._arbitration_engine.finalize(now=self._clock())
                self._arbitration_engine.decision_for(submission.claim_id)
                return _error(503, "service_unavailable")
            if (
                refreshed is None
                or refreshed.device_id != durable_context.device_id
                or refreshed.generation != durable_context.generation
            ):
                self._arbitration_engine.finalize(now=self._clock())
                self._arbitration_engine.decision_for(submission.claim_id)
                return _error(401, "unauthorized")
        self._arbitration_engine.finalize(now=self._clock())
        decision = self._arbitration_engine.decision_for(submission.claim_id)
        if decision is None:
            return _error(503, "service_unavailable")
        if durable_context is not None:
            try:
                refreshed = self._durable_device_context(headers)
            except OSError, RuntimeError, TypeError, ValueError:
                return _error(503, "service_unavailable")
            if (
                refreshed is None
                or refreshed.device_id != durable_context.device_id
                or refreshed.generation != durable_context.generation
            ):
                return _error(401, "unauthorized")
        conversation_handle = None
        if decision.decision == "granted":
            if self._conversation_claim_store is None:
                return _error(503, "service_unavailable")
            try:
                conversation_handle = (
                    self._conversation_claim_store.create_from_decision(
                        decision,
                        credential_generation=(
                            None
                            if durable_context is None
                            else durable_context.generation
                        ),
                    )
                )
            except ConversationClaimConflict:
                return _error(409, "conversation_active")
            except OSError, RuntimeError, TypeError, ValueError:
                return _error(503, "service_unavailable")
            if durable_context is not None:
                try:
                    refreshed = self._durable_device_context(headers)
                except OSError, RuntimeError, TypeError, ValueError:
                    self._discard_new_claim(
                        conversation_handle, durable_context.device_id
                    )
                    return _error(503, "service_unavailable")
                if (
                    refreshed is None
                    or refreshed.device_id != durable_context.device_id
                    or refreshed.generation != durable_context.generation
                ):
                    if not self._discard_new_claim(
                        conversation_handle, durable_context.device_id
                    ):
                        return _error(503, "service_unavailable")
                    return _error(401, "unauthorized")
        payload: dict[str, object] = {
            "schema": 1,
            "claim_id": decision.claim_id,
            "decision": decision.decision,
            "arbitration_id": decision.arbitration_id,
            "configuration_revision": decision.configuration_revision,
        }
        if conversation_handle is not None:
            payload["conversation_handle"] = conversation_handle
        return HTTPResponse(
            200,
            payload,
        )

    def _record_http(
        self,
        method: str,
        path: str,
        status: int,
        started: float,
    ) -> None:
        route = _metric_route(path)
        metric_method = method if method in {"GET", "PUT", "POST"} else "other"
        labels = {
            "method": metric_method,
            "route": route,
        }
        self._metrics.inc(
            "hermes_home_http_requests_total",
            labels={**labels, "status": str(status)},
        )
        self._metrics.observe(
            "hermes_home_http_request_duration_seconds",
            time.perf_counter() - started,
            labels=labels,
        )

    def _record_diagnostic(
        self,
        correlation_id: str,
        *,
        path: str,
        status: int,
        started: float,
        response: HTTPResponse | None = None,
    ) -> None:
        if path == "/metrics" or path.startswith("/api/v1/diagnostics/"):
            return
        if 200 <= status < 300:
            outcome = "completed"
        elif status >= 500:
            outcome = "failed"
        else:
            outcome = "rejected"
        failure_code = None if response is None else _response_error_code(response)
        try:
            self._diagnostics.record(
                DiagnosticEvent.create(
                    correlation_id=correlation_id,
                    source="home",
                    phase="request",
                    outcome=outcome,
                    occurred_at=self._diagnostics.now(),
                    duration_ms=max(0, round((time.perf_counter() - started) * 1000)),
                    failure_code=failure_code,
                    route_class="home",
                    route_id=_metric_route(path),
                )
            )
        except Exception as error:  # noqa: BLE001 - telemetry cannot block HTTP work
            del error
        if path.startswith("/api/v1/devices/") and path.endswith("/health"):
            self._record_health_diagnostic(
                correlation_id,
                path=path,
                started=started,
                response=response,
            )

    def _record_health_diagnostic(
        self,
        correlation_id: str,
        *,
        path: str,
        started: float,
        response: HTTPResponse | None,
    ) -> None:
        if response is None or response.status != 200:
            return
        body = response.body
        if not isinstance(body, Mapping):
            return
        health = body.get("health")
        if not isinstance(health, Mapping):
            return
        status = health.get("status")
        if status not in {"healthy", "degraded", "unavailable"}:
            return
        stages = health.get("stages")
        failure_reason = None
        if isinstance(stages, list):
            for stage in stages:
                if not isinstance(stage, Mapping):
                    continue
                if stage.get("status") != "verified":
                    reason = stage.get("reason")
                    if isinstance(reason, str):
                        failure_reason = reason
                    break
        endpoint_fingerprint = None
        parts = path.split("/")
        if len(parts) == 6 and parts[4]:
            try:
                endpoint_fingerprint = self._diagnostics.fingerprint(parts[4])
            except TypeError, ValueError:
                endpoint_fingerprint = None
        try:
            self._diagnostics.record(
                DiagnosticEvent.create(
                    correlation_id=correlation_id,
                    source="home",
                    phase="health",
                    outcome="completed"
                    if status in {"healthy", "degraded"}
                    else "unavailable",
                    occurred_at=self._diagnostics.now(),
                    duration_ms=max(0, round((time.perf_counter() - started) * 1000)),
                    failure_code=_health_diagnostic_code(failure_reason),
                    route_class="home",
                    route_id="health",
                    health=status,
                    endpoint_fingerprint=endpoint_fingerprint,
                )
            )
        except Exception as error:  # noqa: BLE001 - telemetry cannot block HTTP work
            del error

    def _record_outcome(
        self,
        method: str,
        path: str,
        response: HTTPResponse,
    ) -> None:
        if path == "/api/v1/configuration":
            if method == "GET" and response.status == 200:
                self._record_revision(response)
            elif method == "PUT":
                self._metrics.inc(
                    "hermes_home_configuration_publishes_total",
                    labels={"result": _configuration_result(response)},
                )
                if response.status == 200:
                    self._record_revision(response)
        elif path == "/api/v1/wake-claims" and method == "POST":
            result = _claim_result(response)
            self._metrics.inc(
                "hermes_home_wake_claims_total",
                labels={"result": result},
            )
            if response.status == 200 and isinstance(response.body, dict):
                decision = response.body.get("decision")
                if decision in {"granted", "denied"}:
                    self._metrics.inc(
                        "hermes_home_wake_decisions_total",
                        labels={"decision": decision},
                    )

    def _record_revision(self, response: HTTPResponse) -> None:
        if not isinstance(response.body, dict):
            return
        snapshot = response.body.get("snapshot")
        if isinstance(snapshot, Mapping):
            self._set_revision(snapshot)

    def _set_revision(self, snapshot: Mapping[str, object]) -> None:
        revision = snapshot.get("revision")
        if type(revision) is int and revision >= 0:
            self._metrics.set("hermes_home_configuration_revision", revision)


def _json_object(body: bytes | str) -> dict[str, object]:
    if isinstance(body, bytes):
        if len(body) > MAX_REQUEST_BODY_BYTES:
            raise ValueError("request body is too large")
        body = body.decode("utf-8")
    elif isinstance(body, str) and len(body.encode("utf-8")) > MAX_REQUEST_BODY_BYTES:
        raise ValueError("request body is too large")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise TypeError("JSON body must be an object")
    return value


def _require_schema(request: Mapping[str, object]) -> None:
    if type(request.get("schema")) is not int or request["schema"] != 1:
        raise ValueError("unsupported schema")


def _optional_reason(request: Mapping[str, object]) -> str | None:
    """Treat an omitted reason differently from an explicitly null reason."""
    if "reason" not in request:
        return None
    reason = request["reason"]
    if type(reason) is not str:
        raise ValueError("reason must be a string when supplied")
    return reason


def _credential_error(error: CredentialStateError) -> HTTPResponse:
    status_by_code = {
        "unauthorized": 401,
        "forbidden": 403,
        "not_found": 404,
        "conflict": 409,
        "expired_or_consumed": 410,
        "service_unavailable": 503,
    }
    return _error(status_by_code.get(error.code, 400), error.code)


def _material_payload(material: CredentialMaterial) -> dict[str, object]:
    return {
        "schema": 1,
        "device_id": material.device_id,
        "credential": material.credential,
        "generation": material.generation,
        "expires_at": material.expires_at,
        "scope": {
            "rooms": list(material.scope.rooms),
            "capabilities": list(material.scope.capabilities),
            "wake_mappings": list(material.scope.wake_mappings),
        },
    }


def _available_wake_mapping_ids(snapshot: Mapping[str, object]) -> tuple[str, ...]:
    profile_availability = {
        profile["id"]: profile["available"] for profile in snapshot["profiles"]
    }
    return tuple(
        mapping["id"]
        for mapping in snapshot["wake_mappings"]
        if mapping["active"] and profile_availability.get(mapping["profile_id"], False)
    )


def _approved_wake_mapping_ids(
    snapshot: Mapping[str, object],
    selection: object,
) -> tuple[str, ...]:
    if not isinstance(selection, Mapping) or type(selection.get("mode")) is not str:
        raise ValueError("invalid wake mapping grant")
    mode = selection["mode"]
    if mode == "selected" and set(selection) == {"mode", "ids"}:
        mapping_ids = CredentialScope.from_values(
            rooms=(), capabilities=(), wake_mappings=selection["ids"]
        ).wake_mappings
    elif mode == "all_current_profiles" and set(selection) == {"mode"}:
        mapping_ids = _available_wake_mapping_ids(snapshot)
    else:
        raise ValueError("invalid wake mapping grant")
    if not set(mapping_ids).issubset(_available_wake_mapping_ids(snapshot)):
        raise CredentialValidationError(
            "approved scope references an unavailable wake mapping"
        )
    return mapping_ids


def _device_credential(headers: Mapping[str, str]) -> str:
    for name, value in headers.items():
        if (
            name.lower() == "authorization"
            and isinstance(value, str)
            and value.startswith("Device ")
        ):
            credential = value.removeprefix("Device ")
            if credential:
                return credential
    raise CredentialStateError("unauthorized")


def _is_json_content_type(headers: Mapping[str, str]) -> bool:
    for name, value in headers.items():
        if name.lower() == "content-type" and isinstance(value, str):
            media_type = value.split(";", 1)[0].strip().lower()
            return media_type == "application/json"
    return False


def _claim_submission_error(submission: ClaimSubmission) -> HTTPResponse:
    reason = submission.reason
    if reason == "unauthorized":
        status = 401
        code = "unauthorized"
    elif reason == "forbidden":
        status = 403
        code = "forbidden"
    elif reason == "not_found":
        status = 404
        code = "not_found"
    elif reason == "configuration_migration_required":
        return HTTPResponse(
            409,
            {
                "schema": 1,
                "error": {
                    "code": "configuration_migration_required",
                    "current_revision": submission.current_revision,
                },
            },
        )
    elif reason == "invalid_request":
        status = 400
        code = "invalid_request"
    elif reason in {"stale_configuration", "stale_mapping"}:
        status = 409
        code = reason
    elif reason == "service_unavailable":
        status = 503
        code = "service_unavailable"
    else:
        status = 403
        code = "claim_denied"
    return _error(status, code)


def _error(status: int, code: str) -> HTTPResponse:
    return HTTPResponse(status, {"schema": 1, "error": {"code": code}})


def _watch_unavailable(
    reason: str,
    target: Mapping[str, object],
) -> HTTPResponse:
    return HTTPResponse(
        200,
        {
            "schema": 1,
            "watch": {
                "status": "unavailable",
                "reason": reason,
                "endpoint": {
                    "id": target["id"],
                    "name": target["name"],
                },
            },
        },
    )


def _safe_health_failure(correlation_id: str) -> HealthResult:
    stages = (
        HealthStage(
            name="route",
            status="unavailable",
            reason="probe_failed",
            next_action="retry_health_check",
        ),
        HealthStage(name="authorization", status="verified"),
        HealthStage(
            name="bridge",
            status="unavailable",
            reason="probe_failed",
            next_action="inspect_home_bridge",
        ),
        HealthStage(
            name="standard",
            status="unavailable",
            reason="probe_failed",
            next_action="inspect_standard_gateway",
        ),
        HealthStage(
            name="device_local",
            status="unsupported",
            reason="unsupported",
            next_action="check_endpoint_capabilities",
        ),
    )
    return HealthResult(
        correlation_id=correlation_id,
        status="unavailable",
        stages=stages,
        delivery=HealthDeliveryState(
            status="unavailable", reason="delivery_state_unavailable"
        ),
    )


def _stale_health_failure(correlation_id: str) -> HealthResult:
    stale_stages = tuple(
        HealthStage(
            name=name,
            status="stale",
            reason="stale_target",
            next_action="refresh_configuration",
        )
        for name in ("route", "authorization", "bridge", "standard")
    )
    return HealthResult(
        correlation_id=correlation_id,
        status="unavailable",
        stages=(
            *stale_stages,
            HealthStage(
                name="device_local",
                status="unsupported",
                reason="unsupported",
                next_action="check_endpoint_capabilities",
            ),
        ),
        delivery=HealthDeliveryState(
            status="unavailable", reason="delivery_state_unavailable"
        ),
    )


def _health_diagnostic_code(reason: str | None) -> str | None:
    if reason is None:
        return None
    return {
        "unsupported": "capability_unavailable",
        "route_unauthorized": "unauthorized",
        "route_timeout": "transport_timeout",
        "standard_timeout": "transport_timeout",
        "probe_timeout": "transport_timeout",
        "standard_protocol_error": "protocol_error",
        "authorization_unavailable": "authorization_unavailable",
    }.get(reason, "service_unavailable")


def _metric_route(path: str) -> str:
    if path.startswith("/api/v1/diagnostics/timeline/"):
        return "diagnostics_timeline"
    if path.startswith("/api/v1/devices/") and path.endswith("/watch"):
        return "device_watch"
    if path.startswith("/api/v1/devices/") and path.endswith("/health"):
        return "device_health"
    if path.startswith("/api/v1/devices/"):
        return "device_configuration"
    return {
        "/api/v1/configuration": "configuration",
        "/api/v1/wake-claims": "wake_claims",
        "/api/v1/devices": "device_configuration",
        "/metrics": "metrics",
        "/api/v1/diagnostics/status": "diagnostics_status",
    }.get(path, "other")


def _configuration_result(response: HTTPResponse) -> str:
    if response.status == 200:
        return "success"
    return _response_error_code(response) or f"status_{response.status}"


def _claim_result(response: HTTPResponse) -> str:
    if response.status == 200 and isinstance(response.body, dict):
        decision = response.body.get("decision")
        if decision in {"granted", "denied"}:
            return decision
    return _response_error_code(response) or f"status_{response.status}"


def _response_error_code(response: HTTPResponse) -> str | None:
    if not isinstance(response.body, dict):
        return None
    error = response.body.get("error")
    if not isinstance(error, Mapping):
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None
