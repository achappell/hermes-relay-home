"""Explicit paired-room configuration, scope and household eligibility boundaries."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from hermes_home.domain.configuration import (
    ConfigurationValidationError,
    validate_snapshot,
)
from hermes_home.domain.credentials import (
    AuthenticatedDevice,
    CredentialScope,
    CredentialService,
    CredentialStateError,
)
from hermes_home.domain.paired_room import PairedRoomPolicy, authorize_paired_room
from hermes_home.storage.credentials import InMemoryCredentialStore


def _device(device_id: str, room_id: str) -> dict[str, object]:
    return {
        "id": device_id,
        "name": device_id,
        "room_id": room_id,
        "priority": 1,
        "capabilities": {"wake_claim": True},
    }


def _approval() -> dict[str, object]:
    return {
        "mode": "paired_room",
        "endpoint_id": "approved-endpoint",
        "wake_mapping_ids": ["wake-family"],
    }


def _snapshot() -> dict[str, object]:
    device = _device("approved-puck", "living-room")
    device["wake_admission"] = _approval()
    return {
        "revision": 12,
        "rooms": [
            {"id": "living-room", "name": "Living Room"},
            {"id": "kitchen", "name": "Kitchen"},
        ],
        "profiles": [{"id": "family", "name": "Family", "available": True}],
        "wake_mappings": [
            {
                "id": "wake-family",
                "phrase": "Hey Family",
                "profile_id": "family",
                "active": True,
            },
            {
                "id": "wake-other",
                "phrase": "Hey Other",
                "profile_id": "family",
                "active": True,
            },
        ],
        "devices": [device],
    }


def _context(
    device_id: str = "approved-puck",
    *,
    endpoint_id: str = "approved-endpoint",
    room_id: str = "living-room",
    mapping_ids: tuple[str, ...] = ("wake-family",),
    capabilities: tuple[str, ...] = ("wake_claim",),
    generation: int = 1,
) -> AuthenticatedDevice:
    return AuthenticatedDevice(
        device_id=device_id,
        endpoint_id=endpoint_id,
        generation=generation,
        scope=CredentialScope.from_values(
            rooms=[room_id], capabilities=capabilities, wake_mappings=mapping_ids
        ),
    )


def _schema_validator() -> Draft202012Validator:
    path = Path(__file__).parents[1] / "docs/contracts/v1/configuration.schema.json"
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    "approval",
    [
        None,
        [],
        {},
        {"mode": "paired_room", "endpoint_id": "approved-endpoint"},
        {**_approval(), "mode": "strict"},
        {**_approval(), "mode": True},
        {**_approval(), "room_id": "living-room"},
        {**_approval(), "device_id": "approved-puck"},
        {**_approval(), "score": 1},
        {**_approval(), "endpoint_id": ""},
        {**_approval(), "endpoint_id": "x" * 129},
        {**_approval(), "endpoint_id": 1},
        {**_approval(), "wake_mapping_ids": []},
        {**_approval(), "wake_mapping_ids": "wake-family"},
        {**_approval(), "wake_mapping_ids": ["wake-family", "wake-family"]},
        {**_approval(), "wake_mapping_ids": [""]},
        {**_approval(), "wake_mapping_ids": ["x" * 129]},
        {**_approval(), "wake_mapping_ids": [True]},
    ],
)
def test_malformed_approval_is_rejected_by_configuration_and_schema(approval) -> None:
    snapshot = _snapshot()
    snapshot["devices"][0]["wake_admission"] = approval

    with pytest.raises(ConfigurationValidationError):
        validate_snapshot(snapshot)
    assert not _schema_validator().is_valid(snapshot)


def test_multiple_approvals_are_rejected_even_in_different_rooms() -> None:
    snapshot = _snapshot()
    competitor = _device("second-puck", "kitchen")
    competitor["wake_admission"] = {**_approval(), "endpoint_id": "second-endpoint"}
    snapshot["devices"].append(competitor)

    with pytest.raises(ConfigurationValidationError, match="at most one"):
        validate_snapshot(snapshot)
    assert not _schema_validator().is_valid(snapshot)


@pytest.mark.parametrize("unavailable", ["missing", "inactive", "profile"])
def test_configuration_rejects_unavailable_approval_targets(unavailable: str) -> None:
    snapshot = _snapshot()
    if unavailable == "missing":
        snapshot["devices"][0]["wake_admission"]["wake_mapping_ids"] = ["missing"]
    elif unavailable == "inactive":
        snapshot["wake_mappings"][0]["active"] = False
    else:
        snapshot["profiles"][0]["available"] = False

    with pytest.raises(ConfigurationValidationError, match="unavailable mapping"):
        validate_snapshot(snapshot)


def test_configuration_rejects_approval_for_disabled_wake_device() -> None:
    snapshot = _snapshot()
    snapshot["devices"][0]["capabilities"]["wake_claim"] = False

    with pytest.raises(ConfigurationValidationError, match="requires wake_claim"):
        validate_snapshot(snapshot)
    assert not _schema_validator().is_valid(snapshot)


def test_explicit_singleton_projects_exact_public_contract() -> None:
    snapshot = _snapshot()
    assert validate_snapshot(snapshot) == snapshot
    assert _schema_validator().is_valid(snapshot)
    context = _context()

    policy = authorize_paired_room(
        snapshot,
        context,
        (context,),
        wake_mapping_id="wake-family",
        configuration_revision=12,
    )

    assert policy == PairedRoomPolicy("living-room", ("wake-family",))
    assert policy.to_public() == {
        "mode": "paired_room",
        "room_id": "living-room",
        "wake_mapping_ids": ["wake-family"],
    }
    public = policy.to_public()
    public["wake_mapping_ids"].clear()
    assert policy.wake_mapping_ids == ("wake-family",)


def test_omitted_policy_preserves_strict_configuration_without_autoapproval() -> None:
    snapshot = _snapshot()
    del snapshot["devices"][0]["wake_admission"]
    context = _context()

    assert validate_snapshot(snapshot) == snapshot
    assert _schema_validator().is_valid(snapshot)
    with pytest.raises(CredentialStateError, match="^forbidden$"):
        authorize_paired_room(snapshot, context, (context,))


def test_same_mapping_credential_authorized_competitor_in_another_room_denies() -> None:
    snapshot = _snapshot()
    snapshot["devices"].append(_device("kitchen-puck", "kitchen"))
    context = _context()
    competitor = _context(
        "kitchen-puck", endpoint_id="kitchen-endpoint", room_id="kitchen"
    )

    with pytest.raises(CredentialStateError, match="^forbidden$"):
        authorize_paired_room(snapshot, context, (context, competitor))


@pytest.mark.parametrize(
    "exclusion",
    ["unconfigured", "disabled", "other_mapping", "wrong_room", "no_capability"],
)
def test_ineligible_or_unrelated_mapping_device_does_not_compete(
    exclusion: str,
) -> None:
    snapshot = _snapshot()
    competitor_device = _device("kitchen-puck", "kitchen")
    if exclusion != "unconfigured":
        snapshot["devices"].append(competitor_device)
    if exclusion == "disabled":
        competitor_device["capabilities"]["wake_claim"] = False
    competitor = _context(
        "kitchen-puck",
        endpoint_id="kitchen-endpoint",
        room_id="living-room" if exclusion == "wrong_room" else "kitchen",
        mapping_ids=("wake-other",)
        if exclusion == "other_mapping"
        else ("wake-family",),
        capabilities=() if exclusion == "no_capability" else ("wake_claim",),
    )
    context = _context()

    assert authorize_paired_room(
        snapshot, context, (context, competitor)
    ).to_public() == {
        "mode": "paired_room",
        "room_id": "living-room",
        "wake_mapping_ids": ["wake-family"],
    }
    assert "wake_admission" not in competitor_device


@pytest.mark.parametrize(
    "mismatch",
    [
        "endpoint",
        "room",
        "mapping",
        "capability",
        "generation",
        "active_endpoint",
        "active_scope",
        "inactive_context",
        "disabled_device",
        "unconfigured_device",
        "inactive_mapping",
        "missing_mapping",
        "unavailable_profile",
        "missing_profile",
        "requested_mapping",
    ],
)
def test_authorization_binding_and_availability_mismatches_fail_closed(
    mismatch: str,
) -> None:
    snapshot = _snapshot()
    context = _context()
    active = (context,)
    requested_mapping = "wake-family"
    if mismatch == "endpoint":
        snapshot["devices"][0]["wake_admission"]["endpoint_id"] = "other-endpoint"
    elif mismatch == "room":
        snapshot["devices"][0]["room_id"] = "kitchen"
    elif mismatch == "mapping":
        context = _context(mapping_ids=("wake-other",))
        active = (context,)
    elif mismatch == "capability":
        context = _context(capabilities=("health_view",))
        active = (context,)
    elif mismatch == "generation":
        active = (replace(context, generation=2),)
    elif mismatch == "active_endpoint":
        active = (replace(context, endpoint_id="other-endpoint"),)
    elif mismatch == "active_scope":
        active = (_context(mapping_ids=("wake-other",)),)
    elif mismatch == "inactive_context":
        active = ()
    elif mismatch == "disabled_device":
        snapshot["devices"][0]["capabilities"]["wake_claim"] = False
    elif mismatch == "unconfigured_device":
        snapshot["devices"] = []
    elif mismatch == "inactive_mapping":
        snapshot["wake_mappings"][0]["active"] = False
    elif mismatch == "missing_mapping":
        snapshot["wake_mappings"] = []
    elif mismatch == "unavailable_profile":
        snapshot["profiles"][0]["available"] = False
    elif mismatch == "missing_profile":
        snapshot["profiles"] = []
    elif mismatch == "requested_mapping":
        requested_mapping = "wake-other"

    with pytest.raises(CredentialStateError, match="^forbidden$"):
        authorize_paired_room(
            snapshot, context, active, wake_mapping_id=requested_mapping
        )


@pytest.mark.parametrize("revision", [11, 13, True, 12.0, "12"])
def test_revision_must_match_current_strict_integer(revision) -> None:
    context = _context()

    with pytest.raises(CredentialStateError, match="^stale_configuration$"):
        authorize_paired_room(
            _snapshot(), context, (context,), configuration_revision=revision
        )


def test_policy_projection_intersects_mapping_scope_without_expanding_it() -> None:
    snapshot = _snapshot()
    snapshot["devices"][0]["wake_admission"]["wake_mapping_ids"] = [
        "wake-other",
        "wake-family",
    ]
    context = _context()

    assert authorize_paired_room(snapshot, context, (context,)).wake_mapping_ids == (
        "wake-family",
    )
    with pytest.raises(CredentialStateError, match="^forbidden$"):
        authorize_paired_room(
            snapshot, context, (context,), wake_mapping_id="wake-other"
        )


def _credential_record(
    context: AuthenticatedDevice, *, status: str = "active", expires_at: float = 2_000.0
) -> dict[str, object]:
    return {
        "device_id": context.device_id,
        "endpoint_id": context.endpoint_id,
        "generation": context.generation,
        "status": status,
        "expires_at": expires_at,
        "overlap_until": 1_500.0,
        "digest": "secret-digest-not-for-projection",
        "scope": {
            "rooms": list(context.scope.rooms),
            "capabilities": list(context.scope.capabilities),
            "wake_mappings": list(context.scope.wake_mappings),
        },
    }


def test_active_wake_view_is_current_unexpired_scoped_redacted_and_read_only(
    monkeypatch,
) -> None:
    context = _context()
    current = replace(context, generation=2)
    records = [
        _credential_record(context, status="replaced"),
        _credential_record(current),
        _credential_record(_context("revoked"), status="revoked"),
        _credential_record(_context("expired"), expires_at=1_000.0),
        _credential_record(_context("not-wake", capabilities=("health_view",))),
        _credential_record(_context("superseded")),
        _credential_record(_context("superseded", generation=2), status="revoked"),
    ]
    store = InMemoryCredentialStore()
    store.mutate(lambda state: state["credentials"].extend(records))
    before = store.read_state()
    service = CredentialService(
        store=store, root_secret=b"r" * 32, clock=lambda: 1_000.0
    )

    def reject_write(mutation):
        pytest.fail("active wake authorization must not mutate credential state")

    monkeypatch.setattr(store, "mutate", reject_write)
    active = service.active_wake_devices()

    assert active == (current,)
    assert "secret-digest-not-for-projection" not in repr(active)
    assert store.read_state() == before
    with pytest.raises(CredentialStateError, match="^forbidden$"):
        authorize_paired_room(_snapshot(), context, active)
    assert authorize_paired_room(_snapshot(), current, active).room_id == "living-room"


@pytest.mark.parametrize("status", ["revoked", "replaced", "expired"])
def test_retired_or_expired_competitor_credential_does_not_deny(status: str) -> None:
    context = _context()
    competitor = _context(
        "kitchen-puck", endpoint_id="kitchen-endpoint", room_id="kitchen"
    )
    snapshot = _snapshot()
    snapshot["devices"].append(_device("kitchen-puck", "kitchen"))
    store = InMemoryCredentialStore()
    records = [
        _credential_record(context),
        _credential_record(
            competitor,
            status="active" if status == "expired" else status,
            expires_at=1_000.0 if status == "expired" else 2_000.0,
        ),
    ]
    store.mutate(lambda state: state["credentials"].extend(records))
    service = CredentialService(
        store=store, root_secret=b"r" * 32, clock=lambda: 1_000.0
    )

    assert authorize_paired_room(
        snapshot, context, service.active_wake_devices()
    ).wake_mapping_ids == ("wake-family",)


def test_admission_exclusivity_is_specific_to_the_requested_mapping() -> None:
    snapshot = _snapshot()
    snapshot["devices"][0]["wake_admission"]["wake_mapping_ids"] = [
        "wake-family",
        "wake-other",
    ]
    snapshot["devices"].append(_device("kitchen-puck", "kitchen"))
    context = _context(mapping_ids=("wake-family", "wake-other"))
    competitor = _context(
        "kitchen-puck",
        endpoint_id="kitchen-endpoint",
        room_id="kitchen",
        mapping_ids=("wake-other",),
    )
    active = (context, competitor)

    assert (
        authorize_paired_room(
            snapshot, context, active, wake_mapping_id="wake-family"
        ).room_id
        == "living-room"
    )
    with pytest.raises(CredentialStateError, match="^forbidden$"):
        authorize_paired_room(snapshot, context, active, wake_mapping_id="wake-other")
    with pytest.raises(CredentialStateError, match="^forbidden$"):
        authorize_paired_room(snapshot, context, active)
