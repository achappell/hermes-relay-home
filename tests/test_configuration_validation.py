import pytest

from hermes_home.domain.configuration import (
    ConfigurationValidationError,
    validate_candidate,
    validate_snapshot,
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


def test_protected_device_capabilities_are_explicit_and_normalized() -> None:
    device = _device("puck-screen", "kitchen", 1)
    device["capabilities"] = {
        "wake_claim": True,
        "sensitive_entry": True,
        "consequence_confirm": True,
    }

    normalized = validate_candidate(
        {
            "rooms": [{"id": "kitchen", "name": "Kitchen"}],
            "profiles": [],
            "wake_mappings": [],
            "devices": [device],
        }
    )

    assert normalized["devices"][0]["capabilities"] == {
        "wake_claim": True,
        "sensitive_entry": True,
        "consequence_confirm": True,
    }


@pytest.mark.parametrize("capability", ["sensitive_entry", "consequence_confirm"])
def test_protected_device_capabilities_must_be_boolean(capability: str) -> None:
    device = _device("puck-screen", "kitchen", 1)
    device["capabilities"] = {"wake_claim": True, capability: "yes"}

    with pytest.raises(ConfigurationValidationError, match=capability):
        validate_candidate(
            {
                "rooms": [{"id": "kitchen", "name": "Kitchen"}],
                "profiles": [],
                "wake_mappings": [],
                "devices": [device],
            }
        )


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


def _profile_candidate(profile: dict[str, object]) -> dict[str, object]:
    return {
        "rooms": [],
        "profiles": [profile],
        "wake_mappings": [],
        "devices": [],
    }


def test_profile_shared_flag_is_optional_and_normalized() -> None:
    owned = validate_candidate(
        _profile_candidate({"id": "amanda", "name": "Amanda", "available": True})
    )
    shared = validate_candidate(
        _profile_candidate(
            {"id": "spark", "name": "Spark", "available": True, "shared": True}
        )
    )
    explicit_owned = validate_candidate(
        _profile_candidate(
            {"id": "jensen", "name": "Jensen", "available": True, "shared": False}
        )
    )

    assert "shared" not in owned["profiles"][0]
    assert shared["profiles"][0]["shared"] is True
    assert "shared" not in explicit_owned["profiles"][0]


def test_profile_shared_flag_must_be_a_boolean() -> None:
    with pytest.raises(ConfigurationValidationError, match="shared"):
        validate_candidate(
            _profile_candidate(
                {"id": "spark", "name": "Spark", "available": True, "shared": "yes"}
            )
        )


def _client_candidate(*entries: object) -> dict[str, object]:
    return {
        "rooms": [{"id": "kitchen", "name": "Kitchen"}],
        "profiles": [],
        "wake_mappings": [],
        "devices": [_device("puck-kitchen", "kitchen", 1)],
        "client_devices": list(entries),
    }


def _client_entry(device_id: str, value: object = True) -> dict[str, object]:
    return {"id": device_id, "capabilities": {"interactive_choice": value}}


def test_client_devices_are_optional_and_omitted_when_empty() -> None:
    base = _client_candidate()
    del base["client_devices"]

    assert "client_devices" not in validate_candidate(base)
    assert "client_devices" not in validate_candidate(_client_candidate())
    snapshot = validate_snapshot({"revision": 4, **base})
    assert "client_devices" not in snapshot
    assert validate_snapshot(snapshot) == snapshot


def test_client_devices_normalize_and_round_trip_through_a_snapshot() -> None:
    candidate = _client_candidate(_client_entry("browser-1"))

    normalized = validate_candidate(candidate)
    snapshot = validate_snapshot({"revision": 7, **normalized})

    assert normalized["client_devices"] == [_client_entry("browser-1")]
    assert snapshot == {"revision": 7, **normalized}
    assert validate_snapshot(snapshot) == snapshot


def test_client_devices_keep_an_explicit_false_as_a_recorded_denial() -> None:
    normalized = validate_candidate(
        _client_candidate(_client_entry("browser-1", False))
    )

    assert normalized["client_devices"] == [_client_entry("browser-1", False)]


def test_client_devices_may_name_a_device_that_never_paired() -> None:
    assert validate_candidate(_client_candidate(_client_entry("not-paired-yet")))


def test_client_devices_reject_an_id_that_is_also_a_room_device() -> None:
    with pytest.raises(ConfigurationValidationError, match="Room Device"):
        validate_candidate(_client_candidate(_client_entry("puck-kitchen")))


def test_client_devices_reject_duplicate_ids() -> None:
    with pytest.raises(ConfigurationValidationError, match="duplicate id"):
        validate_candidate(
            _client_candidate(_client_entry("browser-1"), _client_entry("browser-1"))
        )


@pytest.mark.parametrize(
    "entry",
    [
        {"id": "browser-1"},
        {"id": "browser-1", "capabilities": {}},
        {"id": "browser-1", "capabilities": {"interactive_choice": "yes"}},
        {"id": "browser-1", "capabilities": {"interactive_choice": 1}},
        {"id": "browser-1", "capabilities": {"interactive_choice": None}},
        {
            "id": "browser-1",
            "capabilities": {"interactive_choice": True, "sensitive_entry": True},
        },
        {
            "id": "browser-1",
            "capabilities": {"interactive_choice": True, "wake_claim": True},
        },
        {"id": "browser-1", "capabilities": {"interactive_choice": True}, "name": "x"},
        {"id": "", "capabilities": {"interactive_choice": True}},
        {"id": "x" * 129, "capabilities": {"interactive_choice": True}},
        {"id": 7, "capabilities": {"interactive_choice": True}},
        {"id": "browser-1", "capabilities": ["interactive_choice"]},
        "browser-1",
    ],
)
def test_client_devices_reject_malformed_entries(entry: object) -> None:
    with pytest.raises(ConfigurationValidationError):
        validate_candidate(_client_candidate(entry))


@pytest.mark.parametrize("value", [None, {}, "browser-1", 7])
def test_client_devices_must_be_an_array(value: object) -> None:
    candidate = _client_candidate()
    candidate["client_devices"] = value

    with pytest.raises(ConfigurationValidationError, match="client_devices"):
        validate_candidate(candidate)


def test_unknown_top_level_fields_are_still_rejected() -> None:
    candidate = _client_candidate()
    candidate["client_device"] = []

    with pytest.raises(ConfigurationValidationError, match="unknown client_device"):
        validate_candidate(candidate)
