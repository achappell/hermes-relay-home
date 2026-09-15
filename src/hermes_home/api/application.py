"""Framework-independent HTTP contract adapter."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from hermes_home.auth.credentials import PersistentCredentialAuthenticator
from hermes_home.auth.static import StaticCredentialAuthenticator
from hermes_home.domain.arbitration import ArbitrationEngine, ClaimSubmission
from hermes_home.domain.configuration import ConfigurationValidationError
from hermes_home.domain.credentials import (
    CredentialMaterial,
    CredentialScope,
    CredentialService,
    CredentialStateError,
    CredentialValidationError,
)
from hermes_home.observability.metrics import MetricsRegistry
from hermes_home.storage.credentials import CredentialStoreError
from hermes_home.storage.sqlite import ConfigurationStoreError, RevisionConflict

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
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        metrics: MetricsRegistry | None = None,
    ) -> None:
        self._configuration_store = configuration_store
        self._arbitration_engine = arbitration_engine
        self._credential_service = credential_service
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
        try:
            snapshot = self._configuration_store.read()
        except OSError, RuntimeError, TypeError, ValueError:
            pass
        else:
            self._set_revision(snapshot)

    def handle(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | str,
    ) -> HTTPResponse:
        request_path = path.split("?", 1)[0]
        started = time.perf_counter()
        try:
            response = self._dispatch(method, request_path, headers, body)
        except OSError, RuntimeError, TypeError, ValueError:
            self._record_http(method, request_path, 500, started)
            raise
        self._record_http(method, request_path, response.status, started)
        self._record_outcome(method, request_path, response)
        return response

    def _dispatch(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | str,
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
        else:
            parts = path.split("/")
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
            }:
                raise ValueError("invalid scope")
            scope = CredentialScope.from_values(
                rooms=scope_data["rooms"],
                capabilities=scope_data["capabilities"],
            )
            snapshot = self._configuration_store.read()
            configured_rooms = [room["id"] for room in snapshot["rooms"]]
            enrollment = self._credential_service.approve_request(
                request_id,
                scope,
                configured_rooms=configured_rooms,
            )
        except CredentialStateError as error:
            return _credential_error(error)
        except CredentialStoreError:
            return _error(503, "service_unavailable")
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
            reason = request.get("reason")
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
                device_id, reason=request.get("reason")
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

    def _put_configuration(
        self,
        headers: Mapping[str, str],
        body: bytes | str,
    ) -> HTTPResponse:
        if not self._authenticator.authenticate_admin(headers):
            return _error(401, "unauthorized")
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
        return HTTPResponse(200, {"schema": 1, "snapshot": published})

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
        else:
            if durable_context is None:
                return _error(401, "unauthorized")
            authenticated_device_id = durable_context.device_id
        if not _is_json_content_type(headers):
            return _error(400, "invalid_request")
        try:
            claim = _json_object(body)
        except TypeError, ValueError, json.JSONDecodeError:
            return _error(400, "invalid_request")

        submission = self._arbitration_engine.submit(
            claim,
            authenticated_device_id=authenticated_device_id,
            authorized_rooms=(
                None if durable_context is None else durable_context.scope.rooms
            ),
            authorized_wake_claim=(
                None
                if durable_context is None
                else "wake_claim" in durable_context.scope.capabilities
            ),
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
        return HTTPResponse(
            200,
            {
                "schema": 1,
                "claim_id": decision.claim_id,
                "decision": decision.decision,
                "arbitration_id": decision.arbitration_id,
                "configuration_revision": decision.configuration_revision,
            },
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
        },
    }


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
    elif reason == "invalid_request":
        status = 400
        code = "invalid_request"
    elif reason == "service_unavailable":
        status = 503
        code = "service_unavailable"
    else:
        status = 403
        code = "claim_denied"
    return _error(status, code)


def _error(status: int, code: str) -> HTTPResponse:
    return HTTPResponse(status, {"schema": 1, "error": {"code": code}})


def _metric_route(path: str) -> str:
    return {
        "/api/v1/configuration": "configuration",
        "/api/v1/wake-claims": "wake_claims",
        "/metrics": "metrics",
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
