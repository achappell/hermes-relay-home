import itertools

import pytest

from hermes_home.domain.credentials import (
    PENDING_OWNER_GRANT_TTL_SECONDS,
    CredentialScope,
    CredentialService,
    CredentialStateError,
    CredentialValidationError,
)
from hermes_home.storage.credentials import InMemoryCredentialStore

PROFILES = ["amanda", "jensen", "spark"]


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _service(clock: Clock | None = None) -> CredentialService:
    ids = itertools.count(1)
    tokens = itertools.count(1)
    return CredentialService(
        store=InMemoryCredentialStore(),
        root_secret=b"r" * 32,
        clock=clock or Clock(),
        id_factory=lambda: f"id-{next(ids)}",
        token_factory=lambda: f"token-{next(tokens)}",
        confirmation_factory=lambda: "ABCD2345",
    )


def _client_scope() -> CredentialScope:
    return CredentialScope.from_values(rooms=[], capabilities=["client_claim"])


def _pair(
    service: CredentialService,
    *,
    endpoint_id: str,
    profiles: list[str],
    endpoint_type: str = "tui",
    shared: tuple[str, ...] = (),
):
    offer = service.create_offer()
    request = service.submit_request(
        enrollment_code=offer.enrollment_code,
        endpoint_id=endpoint_id,
        label=f"{endpoint_id} label",
        endpoint_type=endpoint_type,
        requested_rooms=[],
        requested_capabilities=["client_claim"],
        secure_storage="platform_secure_store",
    )
    service.approve_request(
        request.request_id,
        _client_scope(),
        configured_rooms=[],
        configured_profiles=PROFILES,
        client_profiles=profiles,
    )
    return service.consume_request(
        request.request_id,
        enrollment_code=offer.enrollment_code,
        secure_storage="platform_secure_store",
        shared_profiles=shared,
    )


def test_first_device_for_an_owned_profile_is_a_recorded_bootstrap() -> None:
    service = _service()

    material = _pair(service, endpoint_id="laptop", profiles=["amanda"])

    (grant,) = material.client_grants
    assert grant.status == "active"
    assert grant.bootstrap is True
    assert grant.profile_id == "amanda"
    assert service.client_grants(material.device_id) == (grant,)


def test_owned_profile_held_elsewhere_waits_for_the_owner() -> None:
    service = _service()
    owner = _pair(service, endpoint_id="jensen-phone", profiles=["jensen"])

    requester = _pair(service, endpoint_id="laptop", profiles=["jensen"])

    (pending,) = requester.client_grants
    assert pending.status == "pending_owner"
    assert service.active_client_grant(requester.device_id, pending.grant_id) is None
    assert service.pending_owner_grants(owner.device_id) == (pending,)
    assert service.pending_owner_grants(requester.device_id) == ()

    approved = service.decide_owner_grant(
        owner.device_id, pending.grant_id, approve=True
    )

    assert approved.status == "active"
    assert service.active_client_grant(requester.device_id, pending.grant_id)


def test_shared_profile_is_active_without_owner_approval() -> None:
    service = _service()
    _pair(service, endpoint_id="phone", profiles=["spark"], shared=("spark",))

    material = _pair(
        service, endpoint_id="laptop", profiles=["spark"], shared=("spark",)
    )

    (grant,) = material.client_grants
    assert grant.status == "active"
    assert grant.bootstrap is False


def test_only_a_holder_of_the_profile_may_decide() -> None:
    service = _service()
    _pair(service, endpoint_id="jensen-phone", profiles=["jensen"])
    outsider = _pair(service, endpoint_id="amanda-phone", profiles=["amanda"])
    requester = _pair(service, endpoint_id="laptop", profiles=["jensen"])
    (pending,) = requester.client_grants

    with pytest.raises(CredentialStateError, match="unauthorized"):
        service.decide_owner_grant(outsider.device_id, pending.grant_id, approve=True)
    with pytest.raises(CredentialStateError, match="unauthorized"):
        service.decide_owner_grant(
            requester.device_id, pending.grant_id, approve=True
        )


def test_owner_rejection_removes_the_pending_grant() -> None:
    service = _service()
    owner = _pair(service, endpoint_id="jensen-phone", profiles=["jensen"])
    requester = _pair(service, endpoint_id="laptop", profiles=["jensen"])
    (pending,) = requester.client_grants

    service.decide_owner_grant(owner.device_id, pending.grant_id, approve=False)

    assert service.client_grants(requester.device_id) == ()


def test_pending_owner_grant_expires_after_a_day() -> None:
    clock = Clock()
    service = _service(clock)
    owner = _pair(service, endpoint_id="jensen-phone", profiles=["jensen"])
    requester = _pair(service, endpoint_id="laptop", profiles=["jensen"])
    (pending,) = requester.client_grants

    clock.now += PENDING_OWNER_GRANT_TTL_SECONDS

    assert service.client_grants(requester.device_id) == ()
    with pytest.raises(CredentialStateError, match="not_found"):
        service.decide_owner_grant(owner.device_id, pending.grant_id, approve=True)


def test_holders_list_every_device_for_the_callers_profiles() -> None:
    service = _service()
    owner = _pair(service, endpoint_id="jensen-phone", profiles=["jensen"])
    requester = _pair(service, endpoint_id="laptop", profiles=["jensen", "amanda"])

    holders = service.profile_holders(owner.device_id)

    assert {(grant.device_id, grant.status) for grant in holders} == {
        (owner.device_id, "active"),
        (requester.device_id, "pending_owner"),
    }


def test_grants_survive_renewal_and_end_on_revocation() -> None:
    clock = Clock()
    service = _service(clock)
    material = _pair(service, endpoint_id="laptop", profiles=["amanda"])
    (grant,) = material.client_grants
    clock.now = material.expires_at - 60

    renewed = service.renew(
        device_id=material.device_id,
        credential=material.credential,
        request_id="renew-1",
        expected_generation=material.generation,
    )

    assert renewed.generation == material.generation + 1
    assert service.active_client_grant(material.device_id, grant.grant_id)

    service.revoke(material.device_id)

    assert service.client_grants(material.device_id) == ()


def test_re_enrollment_replaces_the_devices_grants() -> None:
    service = _service()
    first = _pair(service, endpoint_id="laptop", profiles=["amanda"])

    second = _pair(service, endpoint_id="laptop", profiles=["amanda", "spark"])

    assert second.device_id == first.device_id
    assert {grant.profile_id for grant in service.client_grants(first.device_id)} == {
        "amanda",
        "spark",
    }
    assert first.client_grants[0] not in service.client_grants(first.device_id)


def test_owner_can_revoke_another_devices_grant_for_their_profile() -> None:
    service = _service()
    owner = _pair(service, endpoint_id="jensen-phone", profiles=["jensen"])
    requester = _pair(service, endpoint_id="laptop", profiles=["jensen"])
    (pending,) = requester.client_grants
    service.decide_owner_grant(owner.device_id, pending.grant_id, approve=True)
    outsider = _pair(service, endpoint_id="tablet", profiles=["amanda"])

    with pytest.raises(CredentialStateError, match="unauthorized"):
        service.revoke_client_grant(
            pending.grant_id, requester_device_id=outsider.device_id
        )
    service.revoke_client_grant(pending.grant_id, requester_device_id=owner.device_id)

    assert service.client_grants(requester.device_id) == ()


def test_client_claim_needs_a_personal_client_type_and_a_profile() -> None:
    service = _service()
    offer = service.create_offer()
    request = service.submit_request(
        enrollment_code=offer.enrollment_code,
        endpoint_id="puck",
        label="Kitchen Puck",
        endpoint_type="puck",
        requested_rooms=[],
        requested_capabilities=["client_claim"],
        secure_storage="platform_secure_store",
    )

    with pytest.raises(CredentialValidationError, match="personal client"):
        service.approve_request(
            request.request_id,
            _client_scope(),
            configured_rooms=[],
            configured_profiles=PROFILES,
            client_profiles=["amanda"],
        )


def test_client_profiles_must_be_available_and_non_empty() -> None:
    service = _service()
    offer = service.create_offer()
    request = service.submit_request(
        enrollment_code=offer.enrollment_code,
        endpoint_id="laptop",
        label="Laptop",
        endpoint_type="tui",
        requested_rooms=[],
        requested_capabilities=["client_claim"],
        secure_storage="platform_secure_store",
    )

    with pytest.raises(CredentialValidationError, match="at least one"):
        service.approve_request(
            request.request_id,
            _client_scope(),
            configured_rooms=[],
            configured_profiles=PROFILES,
        )
    with pytest.raises(CredentialValidationError, match="unavailable client"):
        service.approve_request(
            request.request_id,
            _client_scope(),
            configured_rooms=[],
            configured_profiles=PROFILES,
            client_profiles=["nobody"],
        )


def test_rejected_request_reports_rejection_to_the_polling_client() -> None:
    service = _service()
    offer = service.create_offer()
    request = service.submit_request(
        enrollment_code=offer.enrollment_code,
        endpoint_id="laptop",
        label="Laptop",
        endpoint_type="tui",
        requested_rooms=[],
        requested_capabilities=["client_claim"],
        secure_storage="platform_secure_store",
    )
    service.reject_request(request.request_id)

    with pytest.raises(CredentialStateError, match="rejected"):
        service.consume_request(
            request.request_id,
            enrollment_code=offer.enrollment_code,
            secure_storage="platform_secure_store",
        )


def test_paired_devices_show_label_type_and_grants() -> None:
    service = _service()
    material = _pair(service, endpoint_id="laptop", profiles=["amanda"])

    (device,) = service.paired_devices()

    assert device.device_id == material.device_id
    assert device.label == "laptop label"
    assert device.device_type == "tui"
    assert device.status == "active"
    assert [grant.profile_id for grant in device.grants] == ["amanda"]
