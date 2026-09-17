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
        "priority": priority,
        "capabilities": {"wake_claim": True},
    }


def test_priorities_must_be_unique_within_a_room() -> None:
    candidate = {
        "rooms": [{"id": "kitchen", "name": "Kitchen"}],
        "profiles": [],
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
        "profiles": [],
        "wake_mappings": [],
        "devices": [
            _device("puck-kitchen", "kitchen", 1),
            _device("puck-hall", "hall", 1),
        ],
    }

    assert validate_candidate(candidate)["devices"] == candidate["devices"]


def test_interactive_choice_is_an_explicit_device_capability() -> None:
    device = _device("puck-screen", "kitchen", 1)
    device["capabilities"] = {
        "wake_claim": True,
        "interactive_choice": True,
    }
    candidate = {
        "rooms": [{"id": "kitchen", "name": "Kitchen"}],
        "profiles": [],
        "wake_mappings": [],
        "devices": [device],
    }

    normalized = validate_candidate(candidate)

    assert normalized["devices"][0]["capabilities"] == {
        "wake_claim": True,
        "interactive_choice": True,
    }
    legacy = validate_candidate(
        {
            **candidate,
            "devices": [_device("puck-audio", "kitchen", 1)],
        }
    )
    assert legacy["devices"][0]["capabilities"] == {"wake_claim": True}


def test_interactive_choice_capability_must_be_a_boolean() -> None:
    device = _device("puck-screen", "kitchen", 1)
    device["capabilities"] = {"wake_claim": True, "interactive_choice": "yes"}
    candidate = {
        "rooms": [{"id": "kitchen", "name": "Kitchen"}],
        "profiles": [],
        "wake_mappings": [],
        "devices": [device],
    }

    with pytest.raises(ConfigurationValidationError, match="interactive_choice"):
        validate_candidate(candidate)


def test_unknown_fields_are_rejected_from_the_candidate() -> None:
    candidate = {
        "rooms": [],
        "profiles": [],
        "wake_mappings": [],
        "devices": [],
        "credentials": [],
    }

    with pytest.raises(ConfigurationValidationError, match="unknown credentials"):
        validate_candidate(candidate)


def test_active_phrases_are_unique_after_normalization() -> None:
    candidate = {
        "rooms": [],
        "profiles": [
            {"id": "family", "name": "Family", "available": True},
            {"id": "private", "name": "Private", "available": True},
        ],
        "wake_mappings": [
            {
                "id": "family-hey-hermes",
                "phrase": "Hey Hermes",
                "profile_id": "family",
                "active": True,
            },
            {
                "id": "private-hey-hermes",
                "phrase": "  hey   hermes  ",
                "profile_id": "private",
                "active": True,
            },
        ],
        "devices": [],
    }

    with pytest.raises(ConfigurationValidationError, match="duplicates an active"):
        validate_candidate(candidate)


def test_full_width_wake_phrase_collides_after_nfkc_normalization() -> None:
    candidate = {
        "rooms": [],
        "profiles": [
            {"id": "family", "name": "Family", "available": True},
            {"id": "private", "name": "Private", "available": True},
        ],
        "wake_mappings": [
            {
                "id": "ascii",
                "phrase": "Hey Hermes",
                "profile_id": "family",
                "active": True,
            },
            {
                "id": "full-width",
                "phrase": "Ｈｅｙ　Ｈｅｒｍｅｓ",
                "profile_id": "private",
                "active": True,
            },
        ],
        "devices": [],
    }

    with pytest.raises(ConfigurationValidationError, match="duplicates an active"):
        validate_candidate(candidate)


def test_wake_phrase_cannot_normalize_to_empty_even_when_inactive() -> None:
    candidate = {
        "rooms": [],
        "profiles": [{"id": "family", "name": "Family", "available": True}],
        "wake_mappings": [
            {
                "id": "blank",
                "phrase": "\u00a0\u00a0",
                "profile_id": "family",
                "active": False,
            }
        ],
        "devices": [],
    }

    with pytest.raises(ConfigurationValidationError, match="non-whitespace"):
        validate_candidate(candidate)


def test_mapping_may_target_an_unavailable_profile_but_claims_cannot_use_it() -> None:
    candidate = {
        "rooms": [],
        "profiles": [{"id": "family", "name": "Family", "available": False}],
        "wake_mappings": [
            {
                "id": "hey-hermes",
                "phrase": "Hey Hermes",
                "profile_id": "family",
                "active": True,
            }
        ],
        "devices": [],
    }

    assert validate_candidate(candidate) == candidate
