import pytest

from hermes_home.domain.configuration import (
    ConfigurationValidationError,
    validate_candidate,
)


def _device(device_id: str, room_id: str, priority: int) -> dict[str, object]:
    return {
        "id": device_id,
        "name": device_id,
        "room_id": room_id,
        "profile_id": "family",
        "priority": priority,
        "capabilities": {"wake_claim": True},
    }


def test_priorities_must_be_unique_within_a_room() -> None:
    candidate = {
        "rooms": [{"id": "kitchen", "name": "Kitchen"}],
        "wake_mappings": [],
        "devices": [
            _device("puck-a", "kitchen", 1),
            _device("puck-b", "kitchen", 1),
        ],
    }

    with pytest.raises(ConfigurationValidationError, match="duplicate priority"):
        validate_candidate(candidate)


def test_priorities_can_repeat_in_different_rooms() -> None:
    candidate = {
        "rooms": [
            {"id": "kitchen", "name": "Kitchen"},
            {"id": "hall", "name": "Hall"},
        ],
        "wake_mappings": [],
        "devices": [
            _device("puck-kitchen", "kitchen", 1),
            _device("puck-hall", "hall", 1),
        ],
    }

    assert validate_candidate(candidate)["devices"] == candidate["devices"]


def test_unknown_fields_are_rejected_from_the_candidate() -> None:
    candidate = {
        "rooms": [],
        "wake_mappings": [],
        "devices": [],
        "credentials": [],
    }

    with pytest.raises(ConfigurationValidationError, match="unknown credentials"):
        validate_candidate(candidate)
