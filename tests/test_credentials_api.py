import json
import time
from copy import deepcopy

import pytest

from hermes_home.api.application import (
    MAX_REQUEST_BODY_BYTES,
    HomeApplication,
)
from hermes_home.domain.arbitration import ArbitrationEngine
from hermes_home.domain.conversations import InMemoryConversationClaimStore
from hermes_home.domain.credentials import CredentialService
from hermes_home.storage.credentials import SQLiteCredentialStore
from hermes_home.storage.sqlite import SQLiteConfigurationStore

CONFIGURATION = {
    "rooms": [{"id": "kitchen", "name": "Kitchen"}],
    "profiles": [{"id": "family", "name": "Family", "available": True}],
    "wake_mappings": [
        {
            "id": "hey-hermes",
            "phrase": "Hey Hermes",
            "profile_id": "family",
            "active": True,
        }
    ],
    "devices": [
        {
            "id": "device-1",
            "name": "Kitchen Puck",
            "room_id": "kitchen",
            "priority": 1,
            "capabilities": {"wake_claim": True},
        }
    ],
}


def _paired_application(tmp_path, *, device_credentials=None):
    configuration_store = SQLiteConfigurationStore(tmp_path / "home.sqlite3")
    credential_store = SQLiteCredentialStore(tmp_path / "credentials.sqlite3")
    service = CredentialService(
        store=credential_store,
        root_secret=b"r" * 32,
        clock=lambda: 1_000.0,
        id_factory=iter(["offer-1", "request-1", "device-1"]).__next__,
        token_factory=iter(
            ["unused-token", "device-secret", "replacement-secret"]
        ).__next__,
        confirmation_factory=lambda: "ABCD2345",
    )
    engine = ArbitrationEngine(configuration=configuration_store.read)
    application = HomeApplication(
        configuration_store=configuration_store,
        arbitration_engine=engine,
        admin_token="admin-secret",
        device_credentials=device_credentials or {},
        credential_service=service,
        conversation_claim_store=InMemoryConversationClaimStore(
            handle_factory=iter(
                ["opaque-conversation-1", "opaque-conversation-2"]
            ).__next__
        ),
    )
    return application, configuration_store, credential_store


def test_admin_offer_and_endpoint_request_are_reviewable_without_exposing_secrets(
    tmp_path,
) -> None:
    application, configuration_store, credential_store = _paired_application(tmp_path)

    try:
        offer = application.handle(
            "POST",
            "/api/v1/enrollment/offers",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            json.dumps({"schema": 1}).encode(),
        )
        code = offer.body["enrollment_code"]
        request = application.handle(
            "POST",
            "/api/v1/enrollment/requests",
            {"Content-Type": "application/json"},
            json.dumps(
                {
                    "schema": 1,
                    "enrollment_code": code,
                    "endpoint_id": "endpoint-1",
                    "label": "Kitchen Puck",
                    "type": "puck",
                    "requested_rooms": ["kitchen"],
                    "requested_capabilities": ["wake_claim"],
                    "secure_storage": "platform_secure_store",
                }
            ).encode(),
        )
        listed = application.handle(
            "GET",
            "/api/v1/enrollment/requests",
            {"Authorization": "Bearer admin-secret"},
            b"",
        )

        assert offer.status == 200
        assert request.status == 200
        assert listed.status == 200
        assert code not in repr(offer)
        assert listed.body["requests"][0]["status"] == "pending"
        assert code not in json.dumps(listed.body)
        assert "credential" not in json.dumps(listed.body)
        null_reason = application.handle(
            "POST",
            f"/api/v1/enrollment/requests/{request.body['request_id']}/reject",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1, "reason": null}',
        )
        assert null_reason.status == 400
        assert null_reason.body == {
            "schema": 1,
            "error": {"code": "invalid_request"},
        }
        still_pending = application.handle(
            "GET",
            "/api/v1/enrollment/requests",
            {"Authorization": "Bearer admin-secret"},
            b"",
        )
        assert still_pending.body["requests"][0]["status"] == "pending"
        rejected = application.handle(
            "POST",
            f"/api/v1/enrollment/requests/{request.body['request_id']}/reject",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1}',
        )
        assert rejected.status == 200
        assert rejected.body["request"]["status"] == "rejected"
    finally:
        configuration_store.close()
        credential_store.close()


@pytest.mark.parametrize(
    ("method", "path", "headers", "body"),
    [
        (
            "POST",
            "/api/v1/enrollment/offers",
            {"Content-Type": "application/json"},
            b'{"schema": 1}',
        ),
        (
            "GET",
            "/api/v1/enrollment/requests",
            {},
            b"",
        ),
        (
            "POST",
            "/api/v1/enrollment/requests/request-1/approve",
            {"Content-Type": "application/json"},
            b'{"schema": 1, "scope": {"rooms": [], "capabilities": [], "wake_mapping_grant": {"mode": "selected", "ids": []}}}',
        ),
        (
            "POST",
            "/api/v1/enrollment/requests/request-1/reject",
            {"Content-Type": "application/json"},
            b'{"schema": 1}',
        ),
        (
            "POST",
            "/api/v1/devices/device-1/credentials/rotate",
            {"Content-Type": "application/json"},
            b'{"schema": 1, "request_id": "request-1", "generation": 1}',
        ),
        (
            "POST",
            "/api/v1/devices/device-1/revoke",
            {"Content-Type": "application/json"},
            b'{"schema": 1}',
        ),
    ],
)
def test_credential_admin_routes_require_the_admin_credential(
    tmp_path, method, path, headers, body
) -> None:
    application, configuration_store, credential_store = _paired_application(tmp_path)

    try:
        before = credential_store.read_state()
        response = application.handle(method, path, headers, body)

        assert response.status == 401
        assert response.body == {"schema": 1, "error": {"code": "unauthorized"}}
        assert credential_store.read_state() == before
    finally:
        configuration_store.close()
        credential_store.close()


def test_corrupt_enrollment_state_returns_a_redacted_storage_error(tmp_path) -> None:
    application, configuration_store, credential_store = _paired_application(tmp_path)

    try:
        credential_store._connection.execute(
            "UPDATE credential_state SET state = ? WHERE id = 1",
            (
                '{"schema": 1, "offers": [], "requests": [{}], "credentials": [], "replacements": []}',
            ),
        )
        credential_store._connection.commit()

        response = application.handle(
            "GET",
            "/api/v1/enrollment/requests",
            {"Authorization": "Bearer admin-secret"},
            b"",
        )

        assert response.status == 503
        assert response.body == {
            "schema": 1,
            "error": {"code": "service_unavailable"},
        }
    finally:
        configuration_store.close()
        credential_store.close()


def test_malformed_credential_record_returns_a_redacted_storage_error(tmp_path) -> None:
    application, configuration_store, credential_store = _paired_application(tmp_path)

    try:
        credential_store._connection.execute(
            "UPDATE credential_state SET state = ? WHERE id = 1",
            (
                '{"schema": 1, "offers": [], "requests": [], "credentials": [1], "replacements": []}',
            ),
        )
        credential_store._connection.commit()

        response = application.handle(
            "POST",
            "/api/v1/devices/device-1/revoke",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1}',
        )

        assert response.status == 503
        assert response.body == {
            "schema": 1,
            "error": {"code": "service_unavailable"},
        }
    finally:
        configuration_store.close()
        credential_store.close()


def test_control_plane_rejects_an_oversized_json_body(tmp_path) -> None:
    application, configuration_store, credential_store = _paired_application(tmp_path)

    try:
        response = application.handle(
            "POST",
            "/api/v1/enrollment/offers",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b"{}" + b" " * MAX_REQUEST_BODY_BYTES,
        )

        assert response.status == 400
        assert response.body == {
            "schema": 1,
            "error": {"code": "invalid_request"},
        }
    finally:
        configuration_store.close()
        credential_store.close()


def test_approval_and_consumption_issue_a_credential_with_enforced_scope(
    tmp_path,
) -> None:
    application, configuration_store, credential_store = _paired_application(tmp_path)
    configuration_store.replace(expected_revision=0, candidate=CONFIGURATION)

    try:
        offer = application.handle(
            "POST",
            "/api/v1/enrollment/offers",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1}',
        )
        code = offer.body["enrollment_code"]
        request = application.handle(
            "POST",
            "/api/v1/enrollment/requests",
            {"Content-Type": "application/json"},
            json.dumps(
                {
                    "schema": 1,
                    "enrollment_code": code,
                    "endpoint_id": "endpoint-1",
                    "label": "Kitchen Puck",
                    "type": "puck",
                    "requested_rooms": ["kitchen"],
                    "requested_capabilities": ["wake_claim"],
                    "requested_profile_mappings": [],
                    "secure_storage": "platform_secure_store",
                }
            ).encode(),
        )
        request_id = request.body["request_id"]
        approved = application.handle(
            "POST",
            f"/api/v1/enrollment/requests/{request_id}/approve",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1, "scope": {"rooms": ["kitchen"], "capabilities": ["wake_claim"], "wake_mapping_grant": {"mode": "selected", "ids": ["hey-hermes"]}}}',
        )
        consumed = application.handle(
            "POST",
            f"/api/v1/enrollment/requests/{request_id}/consume",
            {"Content-Type": "application/json"},
            json.dumps(
                {
                    "schema": 1,
                    "enrollment_code": code,
                    "secure_storage": "platform_secure_store",
                }
            ).encode(),
        )

        assert approved.status == 200
        assert approved.body["request"]["status"] == "approved"
        assert consumed.status == 200
        assert consumed.body["device_id"] == "device-1"
        assert consumed.body["credential"] == "device-secret"
        assert "device-secret" not in repr(consumed)
        claim = {
            "schema": 1,
            "claim_id": "claim-paired-1",
            "device_id": "device-1",
            "wake_mapping_id": "hey-hermes",
            "configuration_revision": 1,
            "observation": {"detector": "device-local"},
            "acoustic_evidence": {"kind": "opaque-v1", "value": 0.91},
            "availability": "ready",
        }
        claimed = application.handle(
            "POST",
            "/api/v1/wake-claims",
            {
                "Authorization": "Device device-secret",
                "Content-Type": "application/json",
            },
            json.dumps(claim).encode(),
        )
        assert claimed.status == 200
        malformed = application.handle(
            "POST",
            "/api/v1/wake-claims",
            {
                "Authorization": "Device device-secret",
                "Content-Type": "application/json",
            },
            b"{}",
        )
        assert malformed.status == 400
        assert malformed.body == {
            "schema": 1,
            "error": {"code": "invalid_request"},
        }
        rotated = application.handle(
            "POST",
            "/api/v1/devices/device-1/credentials/rotate",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1, "request_id": "admin-rotation-1", "generation": 1}',
        )
        assert rotated.status == 200
        assert rotated.body["credential"] == "replacement-secret"
        assert (
            application._conversation_claim_store._claims["opaque-conversation-1"][
                "status"
            ]
            == "closed"
        )
        assert (
            application._conversation_claim_store._claims["opaque-conversation-1"][
                "close_reason"
            ]
            == "endpoint_revoked"
        )
        old_credential_wake = application.handle(
            "POST",
            "/api/v1/wake-claims",
            {
                "Authorization": "Device device-secret",
                "Content-Type": "application/json",
            },
            json.dumps({**claim, "claim_id": "claim-old-credential"}).encode(),
        )
        assert old_credential_wake.status == 401
        retried_rotation = application.handle(
            "POST",
            "/api/v1/devices/device-1/credentials/renew",
            {
                "Authorization": "Device device-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1, "request_id": "admin-rotation-1", "generation": 1}',
        )
        assert retried_rotation.status == 200
        assert retried_rotation.body["credential"] == "replacement-secret"
        replacement_claim = application.handle(
            "POST",
            "/api/v1/wake-claims",
            {
                "Authorization": "Device replacement-secret",
                "Content-Type": "application/json",
            },
            json.dumps({**claim, "claim_id": "claim-paired-rotated"}).encode(),
        )
        assert replacement_claim.status == 200
        null_reason = application.handle(
            "POST",
            "/api/v1/devices/device-1/revoke",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1, "reason": null}',
        )
        assert null_reason.status == 400
        assert null_reason.body == {
            "schema": 1,
            "error": {"code": "invalid_request"},
        }
        revoked = application.handle(
            "POST",
            "/api/v1/devices/device-1/revoke",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1}',
        )
        assert revoked.status == 200
        assert (
            application._conversation_claim_store._claims["opaque-conversation-2"][
                "status"
            ]
            == "closed"
        )
        assert (
            application._conversation_claim_store._claims["opaque-conversation-2"][
                "close_reason"
            ]
            == "endpoint_revoked"
        )
        rejected_after_revoke = application.handle(
            "POST",
            "/api/v1/wake-claims",
            {
                "Authorization": "Device replacement-secret",
                "Content-Type": "application/json",
            },
            json.dumps({**claim, "claim_id": "claim-paired-2"}).encode(),
        )
        assert rejected_after_revoke.status == 401
        with_request_again = application.handle(
            "POST",
            f"/api/v1/enrollment/requests/{request_id}/consume",
            {"Content-Type": "application/json"},
            json.dumps(
                {
                    "schema": 1,
                    "enrollment_code": code,
                    "secure_storage": "platform_secure_store",
                }
            ).encode(),
        )
        assert with_request_again.status == 410
        assert with_request_again.body == {
            "schema": 1,
            "error": {"code": "expired_or_consumed"},
        }
    finally:
        configuration_store.close()
        credential_store.close()


def test_touch_approval_keeps_binding_in_home_and_admits_the_paired_device(
    tmp_path,
) -> None:
    application, configuration_store, credential_store = _paired_application(tmp_path)
    configuration_store.replace(expected_revision=0, candidate=CONFIGURATION)

    try:
        offer = application.handle(
            "POST",
            "/api/v1/enrollment/offers",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1}',
        )
        request = application.handle(
            "POST",
            "/api/v1/enrollment/requests",
            {"Content-Type": "application/json"},
            json.dumps(
                {
                    "schema": 1,
                    "enrollment_code": offer.body["enrollment_code"],
                    "endpoint_id": "endpoint-1",
                    "label": "Kitchen Touch",
                    "type": "touch",
                    "requested_rooms": ["kitchen"],
                    "requested_capabilities": ["touch_claim"],
                    "secure_storage": "platform_secure_store",
                }
            ).encode(),
        )
        approved = application.handle(
            "POST",
            f"/api/v1/enrollment/requests/{request.body['request_id']}/approve",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            json.dumps(
                {
                    "schema": 1,
                    "scope": {
                        "rooms": ["kitchen"],
                        "capabilities": ["touch_claim"],
                        "wake_mapping_grant": {"mode": "selected", "ids": []},
                        "touch_binding": {
                            "room_id": "kitchen",
                            "profile_id": "family",
                        },
                    },
                }
            ).encode(),
        )
        consumed = application.handle(
            "POST",
            f"/api/v1/enrollment/requests/{request.body['request_id']}/consume",
            {"Content-Type": "application/json"},
            json.dumps(
                {
                    "schema": 1,
                    "enrollment_code": offer.body["enrollment_code"],
                    "secure_storage": "platform_secure_store",
                }
            ).encode(),
        )

        assert approved.status == 200
        assert consumed.status == 200
        assert consumed.body["scope"] == {
            "rooms": ["kitchen"],
            "capabilities": ["touch_claim"],
            "wake_mappings": [],
        }
        assert "profile_id" not in json.dumps(consumed.body)
        configuration = application.handle(
            "GET",
            "/api/v1/devices/device-1/configuration",
            {"Authorization": f"Device {consumed.body['credential']}"},
            b"",
        )
        assert configuration.body == {
            "schema": 1,
            "snapshot": {"revision": 1, "wake_mappings": []},
        }
        claim = application.handle(
            "POST",
            "/api/v1/touch-claims",
            {
                "Authorization": f"Device {consumed.body['credential']}",
                "Content-Type": "application/json",
            },
            json.dumps(
                {
                    "schema": 1,
                    "claim_id": "touch-paired-1",
                    "device_id": "device-1",
                    "configuration_revision": 1,
                    "initiation": {"kind": "tap", "observed_at_ms": 1},
                }
            ).encode(),
        )
        assert claim.status == 200
        assert "profile_id" not in json.dumps(claim.body)
    finally:
        configuration_store.close()
        credential_store.close()


def test_all_current_profile_mapping_approval_is_a_finite_device_snapshot(
    tmp_path,
) -> None:
    application, configuration_store, credential_store = _paired_application(tmp_path)
    candidate = deepcopy(CONFIGURATION)
    candidate["profiles"].append(
        {"id": "unavailable", "name": "Unavailable", "available": False}
    )
    candidate["wake_mappings"].extend(
        [
            {
                "id": "unavailable-map",
                "phrase": "Unavailable profile",
                "profile_id": "unavailable",
                "active": True,
            },
            {
                "id": "inactive-map",
                "phrase": "Inactive mapping",
                "profile_id": "family",
                "active": False,
            },
        ]
    )
    configuration_store.replace(expected_revision=0, candidate=candidate)

    try:
        offer = application.handle(
            "POST",
            "/api/v1/enrollment/offers",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1}',
        )
        request = application.handle(
            "POST",
            "/api/v1/enrollment/requests",
            {"Content-Type": "application/json"},
            json.dumps(
                {
                    "schema": 1,
                    "enrollment_code": offer.body["enrollment_code"],
                    "endpoint_id": "endpoint-1",
                    "label": "Kitchen Puck",
                    "type": "puck",
                    "requested_rooms": ["kitchen"],
                    "requested_capabilities": ["wake_claim"],
                    "secure_storage": "platform_secure_store",
                }
            ).encode(),
        )
        approved = application.handle(
            "POST",
            f"/api/v1/enrollment/requests/{request.body['request_id']}/approve",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1, "scope": {"rooms": ["kitchen"], "capabilities": ["wake_claim"], "wake_mapping_grant": {"mode": "all_current_profiles"}}}',
        )
        consumed = application.handle(
            "POST",
            f"/api/v1/enrollment/requests/{request.body['request_id']}/consume",
            {"Content-Type": "application/json"},
            json.dumps(
                {
                    "schema": 1,
                    "enrollment_code": offer.body["enrollment_code"],
                    "secure_storage": "platform_secure_store",
                }
            ).encode(),
        )

        assert approved.status == 200
        assert consumed.status == 200
        configuration_store.replace(
            expected_revision=1,
            candidate={
                **candidate,
                "wake_mappings": [
                    *candidate["wake_mappings"],
                    {
                        "id": "later-map",
                        "phrase": "Later mapping",
                        "profile_id": "family",
                        "active": True,
                    },
                ],
            },
        )
        response = application.handle(
            "GET",
            "/api/v1/devices/device-1/configuration",
            {"Authorization": f"Device {consumed.body['credential']}"},
            b"",
        )

        assert response.status == 200
        assert response.body == {
            "schema": 1,
            "snapshot": {
                "revision": 2,
                "wake_mappings": [{"id": "hey-hermes", "phrase": "Hey Hermes"}],
            },
        }
        assert "profile" not in json.dumps(response.body).casefold()
    finally:
        configuration_store.close()
        credential_store.close()


def test_paired_wake_claim_cannot_escape_the_approved_room_scope(tmp_path) -> None:
    application, configuration_store, credential_store = _paired_application(tmp_path)
    configuration_store.replace(expected_revision=0, candidate=CONFIGURATION)

    try:
        offer = application.handle(
            "POST",
            "/api/v1/enrollment/offers",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1}',
        )
        request = application.handle(
            "POST",
            "/api/v1/enrollment/requests",
            {"Content-Type": "application/json"},
            json.dumps(
                {
                    "schema": 1,
                    "enrollment_code": offer.body["enrollment_code"],
                    "endpoint_id": "endpoint-1",
                    "label": "Kitchen Puck",
                    "type": "puck",
                    "requested_rooms": ["kitchen"],
                    "requested_capabilities": ["wake_claim"],
                    "secure_storage": "platform_secure_store",
                }
            ).encode(),
        )
        application.handle(
            "POST",
            f"/api/v1/enrollment/requests/{request.body['request_id']}/approve",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1, "scope": {"rooms": [], "capabilities": ["wake_claim"], "wake_mapping_grant": {"mode": "selected", "ids": []}}}',
        )
        consumed = application.handle(
            "POST",
            f"/api/v1/enrollment/requests/{request.body['request_id']}/consume",
            {"Content-Type": "application/json"},
            json.dumps(
                {
                    "schema": 1,
                    "enrollment_code": offer.body["enrollment_code"],
                    "secure_storage": "platform_secure_store",
                }
            ).encode(),
        )

        claim = {
            "schema": 1,
            "claim_id": "claim-out-of-scope",
            "device_id": "device-1",
            "wake_mapping_id": "hey-hermes",
            "configuration_revision": 1,
            "observation": {"detector": "device-local"},
            "acoustic_evidence": {"kind": "opaque-v1", "value": 0.91},
            "availability": "ready",
        }
        response = application.handle(
            "POST",
            "/api/v1/wake-claims",
            {
                "Authorization": f"Device {consumed.body['credential']}",
                "Content-Type": "application/json",
            },
            json.dumps(claim).encode(),
        )

        assert response.status == 403
        assert response.body == {
            "schema": 1,
            "error": {"code": "forbidden"},
        }
    finally:
        configuration_store.close()
        credential_store.close()


def test_revocation_during_arbitration_cannot_complete_a_wake_claim(tmp_path) -> None:
    application, configuration_store, credential_store = _paired_application(tmp_path)
    configuration_store.replace(expected_revision=0, candidate=CONFIGURATION)

    try:
        offer = application.handle(
            "POST",
            "/api/v1/enrollment/offers",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1}',
        )
        request = application.handle(
            "POST",
            "/api/v1/enrollment/requests",
            {"Content-Type": "application/json"},
            json.dumps(
                {
                    "schema": 1,
                    "enrollment_code": offer.body["enrollment_code"],
                    "endpoint_id": "endpoint-1",
                    "label": "Kitchen Puck",
                    "type": "puck",
                    "requested_rooms": ["kitchen"],
                    "requested_capabilities": ["wake_claim"],
                    "secure_storage": "platform_secure_store",
                }
            ).encode(),
        )
        application.handle(
            "POST",
            f"/api/v1/enrollment/requests/{request.body['request_id']}/approve",
            {
                "Authorization": "Bearer admin-secret",
                "Content-Type": "application/json",
            },
            b'{"schema": 1, "scope": {"rooms": ["kitchen"], "capabilities": ["wake_claim"], "wake_mapping_grant": {"mode": "selected", "ids": ["hey-hermes"]}}}',
        )
        consumed = application.handle(
            "POST",
            f"/api/v1/enrollment/requests/{request.body['request_id']}/consume",
            {"Content-Type": "application/json"},
            json.dumps(
                {
                    "schema": 1,
                    "enrollment_code": offer.body["enrollment_code"],
                    "secure_storage": "platform_secure_store",
                }
            ).encode(),
        )
        claim = {
            "schema": 1,
            "claim_id": "claim-revoked-during-window",
            "device_id": "device-1",
            "wake_mapping_id": "hey-hermes",
            "configuration_revision": 1,
            "observation": {"detector": "device-local"},
            "acoustic_evidence": {"kind": "opaque-v1", "value": 0.91},
            "availability": "ready",
        }

        def revoke_during_window(seconds: float) -> None:
            application._credential_service.revoke("device-1")
            time.sleep(seconds + 0.05)

        application._sleeper = revoke_during_window
        response = application.handle(
            "POST",
            "/api/v1/wake-claims",
            {
                "Authorization": f"Device {consumed.body['credential']}",
                "Content-Type": "application/json",
            },
            json.dumps(claim).encode(),
        )

        assert response.status == 401
        assert credential_store.read_state()["credentials"][0]["status"] == "revoked"
    finally:
        configuration_store.close()
        credential_store.close()


def test_paired_auth_store_failure_is_a_service_unavailable_response(tmp_path) -> None:
    application, configuration_store, credential_store = _paired_application(tmp_path)
    credential_store.close()

    try:
        response = application.handle(
            "POST",
            "/api/v1/wake-claims",
            {
                "Authorization": "Device unknown",
                "Content-Type": "application/json",
            },
            b"{}",
        )

        assert response.status == 503
        assert response.body == {
            "schema": 1,
            "error": {"code": "service_unavailable"},
        }
    finally:
        configuration_store.close()


def test_paired_mode_does_not_fall_back_to_static_device_credentials(tmp_path) -> None:
    application, configuration_store, credential_store = _paired_application(
        tmp_path,
        device_credentials={"legacy-secret": "device-1"},
    )

    try:
        response = application.handle(
            "POST",
            "/api/v1/wake-claims",
            {
                "Authorization": "Device legacy-secret",
                "Content-Type": "application/json",
            },
            b"{}",
        )

        assert response.status == 401
        assert response.body == {
            "schema": 1,
            "error": {"code": "unauthorized"},
        }
    finally:
        configuration_store.close()
        credential_store.close()
