"""Framework-independent HTTP contract adapter."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from hermes_home.auth.static import StaticCredentialAuthenticator
from hermes_home.domain.arbitration import ArbitrationEngine, ClaimSubmission
from hermes_home.domain.configuration import ConfigurationValidationError
from hermes_home.observability.metrics import MetricsRegistry
from hermes_home.storage.sqlite import RevisionConflict


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    status: int
    body: dict[str, object] | str
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
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        metrics: MetricsRegistry | None = None,
    ) -> None:
        self._configuration_store = configuration_store
        self._arbitration_engine = arbitration_engine
        self._authenticator = StaticCredentialAuthenticator(
            admin_token=admin_token,
            device_credentials=device_credentials,
        )
        self._clock = clock
        self._sleeper = sleeper
        self._metrics = metrics or MetricsRegistry()

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
        elif path == "/metrics" and method == "GET":
            return self._get_metrics(headers)
        return _error(404, "not_found")

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
        authenticated_device_id = self._authenticator.authenticate_device(headers)
        if authenticated_device_id is None:
            return _error(401, "unauthorized")
        if not _is_json_content_type(headers):
            return _error(400, "invalid_request")
        try:
            claim = _json_object(body)
        except TypeError, ValueError, json.JSONDecodeError:
            return _error(400, "invalid_request")

        submission = self._arbitration_engine.submit(
            claim,
            authenticated_device_id=authenticated_device_id,
        )
        if not submission.accepted:
            return _claim_submission_error(submission)
        if submission.deadline is None:
            return _error(503, "service_unavailable")
        self._sleeper(max(0.0, submission.deadline - self._clock()))
        self._arbitration_engine.finalize(now=self._clock())
        decision = self._arbitration_engine.decision_for(submission.claim_id)
        if decision is None:
            return _error(503, "service_unavailable")
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
            revision = snapshot.get("revision")
            if type(revision) is int and revision >= 0:
                self._metrics.set("hermes_home_configuration_revision", revision)


def _json_object(body: bytes | str) -> dict[str, object]:
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise TypeError("JSON body must be an object")
    return value


def _is_json_content_type(headers: Mapping[str, str]) -> bool:
    for name, value in headers.items():
        if name.lower() == "content-type":
            media_type = value.split(";", 1)[0].strip().lower()
            return media_type == "application/json"
    return False


def _claim_submission_error(submission: ClaimSubmission) -> HTTPResponse:
    reason = submission.reason
    if reason == "unauthorized":
        status = 401
        code = "unauthorized"
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
