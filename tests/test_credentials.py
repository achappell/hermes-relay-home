import pytest

from hermes_home.domain.credentials import (
    CREDENTIAL_TTL_SECONDS,
    CredentialProtector,
    CredentialScope,
    CredentialService,
    CredentialStateError,
    CredentialValidationError,
    TouchBinding,
)
from hermes_home.storage.credentials import (
    InMemoryCredentialStore,
    SQLiteCredentialStore,
)


def test_approved_scope_must_be_a_subset_of_the_requested_scope() -> None:
    requested = CredentialScope.from_values(
        rooms=["kitchen", "hall"],
        capabilities=["wake_claim"],
    )
    approved = CredentialScope.from_values(
        rooms=["kitchen"],
        capabilities=["wake_claim"],
    )

    approved.assert_subset_of(requested)

    with pytest.raises(CredentialValidationError, match="scope"):
        CredentialScope.from_values(
            rooms=["bedroom"],
            capabilities=["wake_claim"],
        ).assert_subset_of(requested)


def test_credential_scope_accepts_protected_capabilities() -> None:
    scope = CredentialScope.from_values(
        rooms=["kitchen"],
        capabilities=["sensitive_entry", "consequence_confirm"],
    )

    assert scope.capabilities == ("sensitive_entry", "consequence_confirm")


def test_touch_binding_is_approved_only_for_a_capable_available_profile(
    tmp_path,
) -> None:
    store = SQLiteCredentialStore(tmp_path / "home.sqlite3")
    service = CredentialService(
        store=store,
        root_secret=b"r" * 32,
        clock=lambda: 1_000.0,
        id_factory=iter(["offer-1", "request-1"]).__next__,
        token_factory=lambda: "enrollment-secret",
        confirmation_factory=lambda: "ABCD2345",
    )

    try:
        offer = service.create_offer()
        request = service.submit_request(
            enrollment_code=offer.enrollment_code,
            endpoint_id="endpoint-1",
            label="Kitchen Touch",
            endpoint_type="touch",
            requested_rooms=["kitchen"],
            requested_capabilities=["touch_claim"],
            secure_storage="platform_secure_store",
        )
        scope = CredentialScope.from_values(
            rooms=["kitchen"],
            capabilities=["touch_claim"],
            touch_binding=TouchBinding("kitchen", "family"),
        )

        with pytest.raises(
            CredentialValidationError, match="unavailable touch profile"
        ):
            service.approve_request(
                request.request_id,
                scope,
                configured_rooms=["kitchen"],
                configured_profiles=["missing"],
            )
        approved = service.approve_request(
            request.request_id,
            scope,
            configured_rooms=["kitchen"],
            configured_profiles=["family"],
        )

        assert approved.approved_scope == scope
    finally:
        store.close()


def test_current_scope_requires_the_active_current_generation(tmp_path) -> None:
    store = SQLiteCredentialStore(tmp_path / "home.sqlite3")
    service = CredentialService(
        store=store,
        root_secret=b"r" * 32,
        clock=lambda: 1_000.0,
        id_factory=iter(["offer-1", "request-1", "device-1"]).__next__,
        token_factory=iter(["enrollment-secret", "device-secret"]).__next__,
        confirmation_factory=lambda: "ABCD2345",
    )

    try:
        offer = service.create_offer()
        request = service.submit_request(
            enrollment_code=offer.enrollment_code,
            endpoint_id="endpoint-1",
            label="Kitchen Puck",
            endpoint_type="puck",
            requested_rooms=["kitchen"],
            requested_capabilities=["sensitive_entry"],
            secure_storage="platform_secure_store",
        )
        service.approve_request(
            request.request_id,
            CredentialScope.from_values(
                rooms=["kitchen"], capabilities=["sensitive_entry"]
            ),
            configured_rooms=["kitchen"],
        )
        material = service.consume_request(
            request.request_id,
            enrollment_code=offer.enrollment_code,
            secure_storage="platform_secure_store",
        )

        assert service.current_scope(material.device_id, material.generation) == (
            material.scope
        )
        assert (
            service.current_scope(material.device_id, material.generation + 1) is None
        )
        service.revoke(material.device_id)
        assert service.current_scope(material.device_id, material.generation) is None
    finally:
        store.close()


def test_scanning_an_offer_creates_a_pending_request_without_granting_access(
    tmp_path,
) -> None:
    store = SQLiteCredentialStore(tmp_path / "home.sqlite3")
    service = CredentialService(
        store=store,
        root_secret=b"r" * 32,
        clock=lambda: 1_000.0,
        id_factory=iter(["offer-1", "request-1"]).__next__,
        token_factory=iter(["enrollment-secret"]).__next__,
        confirmation_factory=iter(["ABCD2345"]).__next__,
    )

    try:
        offer = service.create_offer()
        request = service.submit_request(
            enrollment_code=offer.enrollment_code,
            endpoint_id="endpoint-1",
            label="Kitchen Puck",
            endpoint_type="puck",
            requested_rooms=["kitchen"],
            requested_capabilities=["wake_claim"],
            requested_profile_mappings=[{"profile_id": "family", "label": "Family"}],
            secure_storage="platform_secure_store",
        )

        assert request.status == "pending"
        assert request.confirmation_code == "ABCD2345"
        assert service.authenticate_device("enrollment-secret") is None
        assert offer.enrollment_code not in str(store.read_state())
    finally:
        store.close()


def test_pairing_requires_platform_secure_storage_before_creating_a_request(
    tmp_path,
) -> None:
    store = SQLiteCredentialStore(tmp_path / "home.sqlite3")
    service = CredentialService(
        store=store,
        root_secret=b"r" * 32,
        clock=lambda: 1_000.0,
        id_factory=iter(["offer-1"]).__next__,
        token_factory=iter(["enrollment-secret"]).__next__,
    )

    try:
        offer = service.create_offer()

        with pytest.raises(CredentialValidationError, match="secure storage"):
            service.submit_request(
                enrollment_code=offer.enrollment_code,
                endpoint_id="endpoint-1",
                label="Kitchen Puck",
                endpoint_type="puck",
                requested_rooms=["kitchen"],
                requested_capabilities=["wake_claim"],
                secure_storage="plaintext_file",
            )

        assert store.read_state()["requests"] == []
    finally:
        store.close()


def test_pending_request_cannot_be_consumed_before_admin_approval(tmp_path) -> None:
    store = SQLiteCredentialStore(tmp_path / "home.sqlite3")
    service = CredentialService(
        store=store,
        root_secret=b"r" * 32,
        clock=lambda: 1_000.0,
        id_factory=iter(["offer-1", "request-1"]).__next__,
        token_factory=lambda: "enrollment-secret",
        confirmation_factory=lambda: "ABCD2345",
    )

    try:
        offer = service.create_offer()
        request = service.submit_request(
            enrollment_code=offer.enrollment_code,
            endpoint_id="endpoint-1",
            label="Kitchen Puck",
            endpoint_type="puck",
            requested_rooms=["kitchen"],
            requested_capabilities=["wake_claim"],
            secure_storage="platform_secure_store",
        )

        with pytest.raises(CredentialStateError, match="approval_pending"):
            service.consume_request(
                request.request_id,
                enrollment_code=offer.enrollment_code,
                secure_storage="platform_secure_store",
            )
        assert store.read_state()["credentials"] == []
        assert store.read_state()["requests"][0]["status"] == "pending"
    finally:
        store.close()


def test_secure_storage_is_checked_again_before_credential_issuance(tmp_path) -> None:
    store = SQLiteCredentialStore(tmp_path / "home.sqlite3")
    service = CredentialService(
        store=store,
        root_secret=b"r" * 32,
        clock=lambda: 1_000.0,
        id_factory=iter(["offer-1", "request-1", "device-1"]).__next__,
        token_factory=iter(["enrollment-secret", "device-secret"]).__next__,
        confirmation_factory=lambda: "ABCD2345",
    )

    try:
        offer = service.create_offer()
        request = service.submit_request(
            enrollment_code=offer.enrollment_code,
            endpoint_id="endpoint-1",
            label="Kitchen Puck",
            endpoint_type="puck",
            requested_rooms=["kitchen"],
            requested_capabilities=["wake_claim"],
            secure_storage="platform_secure_store",
        )
        service.approve_request(
            request.request_id,
            CredentialScope.from_values(rooms=["kitchen"], capabilities=["wake_claim"]),
            configured_rooms=["kitchen"],
        )

        with pytest.raises(CredentialValidationError, match="secure storage"):
            service.consume_request(
                request.request_id,
                enrollment_code=offer.enrollment_code,
                secure_storage="plaintext_file",
            )
        assert store.read_state()["credentials"] == []
        assert store.read_state()["requests"][0]["status"] == "approved"
    finally:
        store.close()


def test_admin_cannot_approve_a_scope_broader_than_the_request(tmp_path) -> None:
    store = SQLiteCredentialStore(tmp_path / "home.sqlite3")
    service = CredentialService(
        store=store,
        root_secret=b"r" * 32,
        clock=lambda: 1_000.0,
        id_factory=iter(["offer-1", "request-1"]).__next__,
        token_factory=lambda: "enrollment-secret",
        confirmation_factory=lambda: "ABCD2345",
    )

    try:
        offer = service.create_offer()
        request = service.submit_request(
            enrollment_code=offer.enrollment_code,
            endpoint_id="endpoint-1",
            label="Kitchen Puck",
            endpoint_type="puck",
            requested_rooms=["kitchen"],
            requested_capabilities=["wake_claim"],
            secure_storage="platform_secure_store",
        )

        with pytest.raises(CredentialValidationError, match="exceeds requested"):
            service.approve_request(
                request.request_id,
                CredentialScope.from_values(
                    rooms=["kitchen", "hall"], capabilities=["wake_claim"]
                ),
                configured_rooms=["kitchen", "hall"],
            )
        assert store.read_state()["requests"][0]["status"] == "pending"
    finally:
        store.close()


def test_approved_request_consumes_once_and_issues_a_scoped_credential(
    tmp_path,
) -> None:
    store = SQLiteCredentialStore(tmp_path / "home.sqlite3")
    identifiers = iter(["offer-1", "request-1", "device-1"])
    tokens = iter(["enrollment-secret", "device-secret"])
    service = CredentialService(
        store=store,
        root_secret=b"r" * 32,
        clock=lambda: 1_000.0,
        id_factory=identifiers.__next__,
        token_factory=tokens.__next__,
        confirmation_factory=lambda: "ABCD2345",
    )

    try:
        offer = service.create_offer()
        request = service.submit_request(
            enrollment_code=offer.enrollment_code,
            endpoint_id="endpoint-1",
            label="Kitchen Puck",
            endpoint_type="puck",
            requested_rooms=["kitchen"],
            requested_capabilities=["wake_claim"],
            secure_storage="platform_secure_store",
        )
        approved = service.approve_request(
            request.request_id,
            CredentialScope.from_values(
                rooms=["kitchen"],
                capabilities=["wake_claim"],
            ),
            configured_rooms=["kitchen"],
        )

        material = service.consume_request(
            approved.request_id,
            enrollment_code=offer.enrollment_code,
            secure_storage="platform_secure_store",
        )

        assert material.device_id == "device-1"
        assert material.generation == 1
        assert material.credential == "device-secret"
        assert service.authenticate_device("device-secret").scope.rooms == ("kitchen",)
        with pytest.raises(CredentialStateError, match="expired_or_consumed"):
            service.consume_request(
                approved.request_id,
                enrollment_code=offer.enrollment_code,
                secure_storage="platform_secure_store",
            )
    finally:
        store.close()


def test_rotation_is_idempotent_and_old_credential_has_a_bounded_overlap(
    tmp_path,
) -> None:
    now = [1_000.0]
    store = SQLiteCredentialStore(tmp_path / "home.sqlite3")
    identifiers = iter(["offer-1", "request-1", "device-1"])
    tokens = iter(["enrollment-secret", "device-secret", "replacement-secret"])
    service = CredentialService(
        store=store,
        root_secret=b"r" * 32,
        clock=lambda: now[0],
        id_factory=identifiers.__next__,
        token_factory=tokens.__next__,
        confirmation_factory=lambda: "ABCD2345",
    )

    try:
        offer = service.create_offer()
        request = service.submit_request(
            enrollment_code=offer.enrollment_code,
            endpoint_id="endpoint-1",
            label="Kitchen Puck",
            endpoint_type="puck",
            requested_rooms=["kitchen"],
            requested_capabilities=["wake_claim"],
            secure_storage="platform_secure_store",
        )
        service.approve_request(
            request.request_id,
            CredentialScope.from_values(rooms=["kitchen"], capabilities=["wake_claim"]),
            configured_rooms=["kitchen"],
        )
        initial = service.consume_request(
            request.request_id,
            enrollment_code=offer.enrollment_code,
            secure_storage="platform_secure_store",
        )
        now[0] += CREDENTIAL_TTL_SECONDS - 1

        replacement = service.renew(
            device_id=initial.device_id,
            credential=initial.credential,
            request_id="rotation-1",
            expected_generation=initial.generation,
        )
        retried = service.renew(
            device_id=initial.device_id,
            credential=initial.credential,
            request_id="rotation-1",
            expected_generation=initial.generation,
        )

        assert replacement.credential == "replacement-secret"
        assert retried == replacement
        assert "replacement-secret" not in str(store.read_state())
        assert service.authenticate_device(initial.credential) is None
        assert service.authenticate_device(replacement.credential).generation == 2
        with pytest.raises(CredentialStateError, match="conflict"):
            service.rotate(
                device_id=initial.device_id,
                request_id="rotation-1",
                expected_generation=replacement.generation,
            )
        with pytest.raises(CredentialStateError, match="conflict"):
            service.renew(
                device_id=initial.device_id,
                credential=initial.credential,
                request_id="rotation-2",
                expected_generation=initial.generation,
            )

        now[0] += 601
        assert service.authenticate_device(initial.credential) is None
        assert service.authenticate_device(replacement.credential) is not None
        replacement_record = store.read_state()["replacements"][0]
        assert replacement_record["status"] == "expired"
        assert replacement_record["ciphertext"] == ""
    finally:
        store.close()


def test_revocation_invalidates_credentials_and_notifies_after_commit(tmp_path) -> None:
    notifications = []
    observed_statuses = []
    now = [1_000.0]
    store = SQLiteCredentialStore(tmp_path / "home.sqlite3")
    identifiers = iter(["offer-1", "request-1", "device-1"])
    tokens = iter(["enrollment-secret", "device-secret"])

    class Observer:
        def on_revoked(self, event) -> None:
            notifications.append(event)
            observed_statuses.append(store.read_state()["credentials"][0]["status"])

    service = CredentialService(
        store=store,
        root_secret=b"r" * 32,
        clock=lambda: now[0],
        id_factory=identifiers.__next__,
        token_factory=tokens.__next__,
        confirmation_factory=lambda: "ABCD2345",
        revocation_observer=Observer(),
    )

    try:
        offer = service.create_offer()
        request = service.submit_request(
            enrollment_code=offer.enrollment_code,
            endpoint_id="endpoint-1",
            label="Kitchen Puck",
            endpoint_type="puck",
            requested_rooms=["kitchen"],
            requested_capabilities=["wake_claim"],
            secure_storage="platform_secure_store",
        )
        service.approve_request(
            request.request_id,
            CredentialScope.from_values(rooms=["kitchen"], capabilities=["wake_claim"]),
            configured_rooms=["kitchen"],
        )
        initial = service.consume_request(
            request.request_id,
            enrollment_code=offer.enrollment_code,
            secure_storage="platform_secure_store",
        )

        event = service.revoke(initial.device_id, reason="lost endpoint")

        assert event.device_id == initial.device_id
        assert event.generation == initial.generation
        assert event.reason == "lost endpoint"
        assert notifications == [event]
        assert observed_statuses == ["revoked"]
        assert service.authenticate_device(initial.credential) is None
        assert all(
            record["status"] == "revoked"
            for record in store.read_state()["credentials"]
        )
        with pytest.raises(CredentialStateError, match="conflict"):
            service.revoke(initial.device_id, reason="again")
        assert notifications == [event]
    finally:
        store.close()


def test_admin_rotation_can_replace_an_active_credential_early(tmp_path) -> None:
    store = SQLiteCredentialStore(tmp_path / "home.sqlite3")
    identifiers = iter(["offer-1", "request-1", "device-1"])
    tokens = iter(["enrollment-secret", "device-secret", "replacement-secret"])
    service = CredentialService(
        store=store,
        root_secret=b"r" * 32,
        clock=lambda: 1_000.0,
        id_factory=identifiers.__next__,
        token_factory=tokens.__next__,
        confirmation_factory=lambda: "ABCD2345",
    )

    try:
        offer = service.create_offer()
        request = service.submit_request(
            enrollment_code=offer.enrollment_code,
            endpoint_id="endpoint-1",
            label="Kitchen Puck",
            endpoint_type="puck",
            requested_rooms=["kitchen"],
            requested_capabilities=["wake_claim"],
            secure_storage="platform_secure_store",
        )
        service.approve_request(
            request.request_id,
            CredentialScope.from_values(rooms=["kitchen"], capabilities=["wake_claim"]),
            configured_rooms=["kitchen"],
        )
        initial = service.consume_request(
            request.request_id,
            enrollment_code=offer.enrollment_code,
            secure_storage="platform_secure_store",
        )

        replacement = service.rotate(
            device_id=initial.device_id,
            request_id="admin-rotation-1",
            expected_generation=initial.generation,
        )

        assert replacement.credential == "replacement-secret"
        assert replacement.generation == 2
        assert service.authenticate_device(initial.credential) is None
    finally:
        store.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ciphertext", "tampered"),
        ("request_id", "other-request"),
        ("device_id", "other-device"),
        ("generation", 2),
    ],
)
def test_tampered_rotation_material_fails_closed(tmp_path, field, value) -> None:
    protector = CredentialProtector(b"r" * 32)
    encrypted = protector.encrypt_replacement(
        "replacement-secret",
        device_id="device-1",
        generation=1,
        request_id="rotation-1",
    )
    record = {
        **encrypted,
        "device_id": "device-1",
        "generation": 1,
        "request_id": "rotation-1",
    }
    record[field] = value

    with pytest.raises(CredentialStateError, match="service_unavailable"):
        protector.decrypt_replacement(record)


def test_active_credential_expires_at_the_ninety_day_boundary(tmp_path) -> None:
    now = [1_000.0]
    store = SQLiteCredentialStore(tmp_path / "home.sqlite3")
    service = CredentialService(
        store=store,
        root_secret=b"r" * 32,
        clock=lambda: now[0],
        id_factory=iter(["offer-1", "request-1", "device-1"]).__next__,
        token_factory=iter(["enrollment-secret", "device-secret"]).__next__,
        confirmation_factory=lambda: "ABCD2345",
    )

    try:
        offer = service.create_offer()
        request = service.submit_request(
            enrollment_code=offer.enrollment_code,
            endpoint_id="endpoint-1",
            label="Kitchen Puck",
            endpoint_type="puck",
            requested_rooms=["kitchen"],
            requested_capabilities=["wake_claim"],
            secure_storage="platform_secure_store",
        )
        service.approve_request(
            request.request_id,
            CredentialScope.from_values(rooms=["kitchen"], capabilities=["wake_claim"]),
            configured_rooms=["kitchen"],
        )
        material = service.consume_request(
            request.request_id,
            enrollment_code=offer.enrollment_code,
            secure_storage="platform_secure_store",
        )
        now[0] += CREDENTIAL_TTL_SECONDS

        assert service.authenticate_device(material.credential) is None
    finally:
        store.close()


def test_authentication_uses_a_read_only_credential_store() -> None:
    class ReadOnlyStore:
        def __init__(self) -> None:
            self._store = InMemoryCredentialStore()
            self.sealed = False

        def read_state(self):
            return self._store.read_state()

        def mutate(self, mutation):
            if self.sealed:
                raise AssertionError("authentication opened a write transaction")
            return self._store.mutate(mutation)

    root_secret = b"r" * 32
    protector = CredentialProtector(root_secret)
    store = ReadOnlyStore()
    store.mutate(
        lambda state: state["credentials"].append(
            {
                "device_id": "device-1",
                "endpoint_id": "endpoint-1",
                "generation": 1,
                "status": "active",
                "issued_at": 1_000.0,
                "expires_at": 2_000.0,
                "digest": protector.digest("device-secret"),
                "scope": {"rooms": ["kitchen"], "capabilities": ["wake_claim"]},
            }
        )
    )
    store.sealed = True
    service = CredentialService(
        store=store,
        root_secret=root_secret,
        clock=lambda: 1_000.0,
    )

    authenticated = service.authenticate_device("device-secret")

    assert authenticated is not None
    assert authenticated.device_id == "device-1"


def test_early_renewal_is_rejected_without_creating_replacement(tmp_path) -> None:
    store = SQLiteCredentialStore(tmp_path / "home.sqlite3")
    service = CredentialService(
        store=store,
        root_secret=b"r" * 32,
        clock=lambda: 1_000.0,
        id_factory=iter(["offer-1", "request-1", "device-1"]).__next__,
        token_factory=iter(["enrollment-secret", "device-secret"]).__next__,
        confirmation_factory=lambda: "ABCD2345",
    )

    try:
        offer = service.create_offer()
        request = service.submit_request(
            enrollment_code=offer.enrollment_code,
            endpoint_id="endpoint-1",
            label="Kitchen Puck",
            endpoint_type="puck",
            requested_rooms=["kitchen"],
            requested_capabilities=["wake_claim"],
            secure_storage="platform_secure_store",
        )
        service.approve_request(
            request.request_id,
            CredentialScope.from_values(rooms=["kitchen"], capabilities=["wake_claim"]),
            configured_rooms=["kitchen"],
        )
        material = service.consume_request(
            request.request_id,
            enrollment_code=offer.enrollment_code,
            secure_storage="platform_secure_store",
        )

        with pytest.raises(CredentialStateError, match="conflict"):
            service.renew(
                device_id=material.device_id,
                credential=material.credential,
                request_id="too-early",
                expected_generation=material.generation,
            )
        assert store.read_state()["replacements"] == []
    finally:
        store.close()


def test_reenrollment_keeps_endpoint_binding_but_invalidates_the_old_credential(
    tmp_path,
) -> None:
    notifications = []
    store = SQLiteCredentialStore(tmp_path / "home.sqlite3")
    identifiers = iter(["offer-1", "request-1", "device-1", "offer-2", "request-2"])
    tokens = iter(
        [
            "enrollment-secret-1",
            "device-secret-1",
            "replacement-secret",
            "enrollment-secret-2",
            "device-secret-2",
        ]
    )
    service = CredentialService(
        store=store,
        root_secret=b"r" * 32,
        clock=lambda: 1_000.0,
        id_factory=identifiers.__next__,
        token_factory=tokens.__next__,
        confirmation_factory=iter(["ABCD2345", "EFGH6789"]).__next__,
        revocation_observer=type(
            "Observer", (), {"on_revoked": notifications.append}
        )(),
    )

    def pair(code: str, endpoint_token: str):
        request = service.submit_request(
            enrollment_code=code,
            endpoint_id="endpoint-1",
            label="Kitchen Puck",
            endpoint_type="puck",
            requested_rooms=["kitchen"],
            requested_capabilities=["wake_claim"],
            secure_storage="platform_secure_store",
        )
        service.approve_request(
            request.request_id,
            CredentialScope.from_values(rooms=["kitchen"], capabilities=["wake_claim"]),
            configured_rooms=["kitchen"],
        )
        return service.consume_request(
            request.request_id,
            enrollment_code=code,
            secure_storage="platform_secure_store",
        )

    try:
        first_offer = service.create_offer()
        first = pair(first_offer.enrollment_code, "enrollment-secret-1")
        replacement = service.rotate(
            device_id=first.device_id,
            request_id="rotation-1",
            expected_generation=first.generation,
        )
        second_offer = service.create_offer()
        second = pair(second_offer.enrollment_code, "enrollment-secret-2")

        assert second.device_id == first.device_id
        assert second.generation == 3
        assert replacement.credential == "replacement-secret"
        assert service.authenticate_device(first.credential) is None
        assert service.authenticate_device(second.credential).generation == 3
        replacement_record = store.read_state()["replacements"][0]
        assert replacement_record["status"] == "revoked"
        assert replacement_record["ciphertext"] == ""
        assert len(notifications) == 1
        assert notifications[0].device_id == first.device_id
        assert notifications[0].generation == replacement.generation
        assert notifications[0].reason == "re-enrollment"
        with pytest.raises(CredentialStateError, match="expired_or_consumed"):
            service.rotate(
                device_id=first.device_id,
                request_id="rotation-1",
                expected_generation=first.generation,
            )
    finally:
        store.close()


def test_listing_expires_a_pending_request_after_its_offer_deadline(tmp_path) -> None:
    now = [1_000.0]
    store = SQLiteCredentialStore(tmp_path / "home.sqlite3")
    service = CredentialService(
        store=store,
        root_secret=b"r" * 32,
        clock=lambda: now[0],
        id_factory=iter(["offer-1", "request-1"]).__next__,
        token_factory=lambda: "enrollment-secret",
        confirmation_factory=lambda: "ABCD2345",
    )

    try:
        offer = service.create_offer()
        request = service.submit_request(
            enrollment_code=offer.enrollment_code,
            endpoint_id="endpoint-1",
            label="Kitchen Puck",
            endpoint_type="puck",
            requested_rooms=["kitchen"],
            requested_capabilities=["wake_claim"],
            secure_storage="platform_secure_store",
        )
        now[0] += 301

        listed = service.list_requests()

        assert listed[0].status == "expired"
        assert store.read_state()["offers"][0]["status"] == "expired"
        with pytest.raises(CredentialStateError, match="expired_or_consumed"):
            service.approve_request(
                request.request_id,
                CredentialScope.from_values(
                    rooms=["kitchen"], capabilities=["wake_claim"]
                ),
                configured_rooms=["kitchen"],
            )
    finally:
        store.close()


def test_rejecting_an_expired_pending_request_does_not_reopen_it(tmp_path) -> None:
    now = [1_000.0]
    store = SQLiteCredentialStore(tmp_path / "home.sqlite3")
    service = CredentialService(
        store=store,
        root_secret=b"r" * 32,
        clock=lambda: now[0],
        id_factory=iter(["offer-1", "request-1"]).__next__,
        token_factory=lambda: "enrollment-secret",
        confirmation_factory=lambda: "ABCD2345",
    )

    try:
        offer = service.create_offer()
        request = service.submit_request(
            enrollment_code=offer.enrollment_code,
            endpoint_id="endpoint-1",
            label="Kitchen Puck",
            endpoint_type="puck",
            requested_rooms=["kitchen"],
            requested_capabilities=["wake_claim"],
            secure_storage="platform_secure_store",
        )
        now[0] += 301

        with pytest.raises(CredentialStateError, match="expired_or_consumed"):
            service.reject_request(request.request_id)
        assert service.list_requests()[0].status == "expired"
    finally:
        store.close()


def test_reopening_the_credential_store_recovers_active_and_revoked_state(
    tmp_path,
) -> None:
    database = tmp_path / "home.sqlite3"
    store = SQLiteCredentialStore(database)
    identifiers = iter(["offer-1", "request-1", "device-1"])
    tokens = iter(["enrollment-secret", "device-secret"])
    service = CredentialService(
        store=store,
        root_secret=b"r" * 32,
        clock=lambda: 1_000.0,
        id_factory=identifiers.__next__,
        token_factory=tokens.__next__,
        confirmation_factory=lambda: "ABCD2345",
    )

    try:
        offer = service.create_offer()
        request = service.submit_request(
            enrollment_code=offer.enrollment_code,
            endpoint_id="endpoint-1",
            label="Kitchen Puck",
            endpoint_type="puck",
            requested_rooms=["kitchen"],
            requested_capabilities=["wake_claim"],
            secure_storage="platform_secure_store",
        )
        service.approve_request(
            request.request_id,
            CredentialScope.from_values(rooms=["kitchen"], capabilities=["wake_claim"]),
            configured_rooms=["kitchen"],
        )
        material = service.consume_request(
            request.request_id,
            enrollment_code=offer.enrollment_code,
            secure_storage="platform_secure_store",
        )
    finally:
        store.close()

    reopened = SQLiteCredentialStore(database)
    recovered = CredentialService(
        store=reopened,
        root_secret=b"r" * 32,
        clock=lambda: 1_000.0,
    )
    try:
        assert (
            recovered.authenticate_device(material.credential).device_id == "device-1"
        )
        recovered.revoke(material.device_id, reason="retired")
    finally:
        reopened.close()

    reopened_again = SQLiteCredentialStore(database)
    recovered_again = CredentialService(
        store=reopened_again,
        root_secret=b"r" * 32,
        clock=lambda: 1_000.0,
    )
    try:
        assert recovered_again.authenticate_device(material.credential) is None
    finally:
        reopened_again.close()


def _android_accepts(credential: str) -> bool:
    """Mirror Android's HomeCredentialValidator: 43 base64url chars, 32 bytes."""
    import base64
    import re

    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", credential):
        return False
    return len(base64.urlsafe_b64decode(credential + "=")) == 32


def test_device_credentials_use_the_32_byte_wire_representation() -> None:
    clock = [1_000.0]
    service = CredentialService(
        store=InMemoryCredentialStore(),
        root_secret=b"r" * 32,
        clock=lambda: clock[0],
    )
    offer = service.create_offer()
    request = service.submit_request(
        enrollment_code=offer.enrollment_code,
        endpoint_id="pixel",
        label="Pixel",
        endpoint_type="android",
        requested_rooms=[],
        requested_capabilities=["client_claim"],
        secure_storage="platform_secure_store",
    )
    service.approve_request(
        request.request_id,
        CredentialScope.from_values(rooms=[], capabilities=["client_claim"]),
        configured_rooms=[],
        configured_profiles=["amanda"],
        client_profiles=["amanda"],
    )

    material = service.consume_request(
        request.request_id,
        enrollment_code=offer.enrollment_code,
        secure_storage="platform_secure_store",
    )
    clock[0] = material.expires_at - 60
    renewed = service.renew(
        device_id=material.device_id,
        credential=material.credential,
        request_id="renew-1",
        expected_generation=material.generation,
    )

    assert _android_accepts(material.credential)
    assert _android_accepts(renewed.credential)
    assert len(offer.enrollment_code) == 32
