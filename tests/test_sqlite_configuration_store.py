import json
import threading
from copy import deepcopy

import pytest

from hermes_home.domain.configuration import ConfigurationValidationError
from hermes_home.storage.sqlite import (
    ConfigurationMigrationRequired,
    RevisionConflict,
    SQLiteConfigurationStore,
)

VALID_CANDIDATE = {
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
            "id": "puck-kitchen",
            "name": "Kitchen Puck",
            "room_id": "kitchen",
            "priority": 1,
            "capabilities": {"wake_claim": True},
        }
    ],
}


def test_new_store_returns_empty_revision_zero_configuration(tmp_path) -> None:
    store = SQLiteConfigurationStore(tmp_path / "home.sqlite3")

    try:
        assert store.read() == {
            "revision": 0,
            "rooms": [],
            "profiles": [],
            "wake_mappings": [],
            "devices": [],
        }
    finally:
        store.close()


def test_publish_activates_a_new_revisioned_configuration(tmp_path) -> None:
    store = SQLiteConfigurationStore(tmp_path / "home.sqlite3")

    try:
        candidate = deepcopy(VALID_CANDIDATE)
        published = store.replace(expected_revision=0, candidate=candidate)

        assert published == {"revision": 1, **VALID_CANDIDATE}
        candidate["rooms"].clear()
        assert store.read() == published
    finally:
        store.close()


def test_stale_publish_raises_conflict_and_preserves_active_configuration(
    tmp_path,
) -> None:
    store = SQLiteConfigurationStore(tmp_path / "home.sqlite3")

    try:
        active = store.replace(expected_revision=0, candidate=VALID_CANDIDATE)
        stale_candidate = {
            **VALID_CANDIDATE,
            "rooms": [{"id": "hall", "name": "Hall"}],
            "devices": [
                {
                    **VALID_CANDIDATE["devices"][0],
                    "room_id": "hall",
                }
            ],
        }

        with pytest.raises(RevisionConflict) as raised:
            store.replace(expected_revision=0, candidate=stale_candidate)

        assert raised.value.current_revision == 1
        assert store.read() == active
    finally:
        store.close()


def test_invalid_publish_is_rejected_without_mutating_active_configuration(
    tmp_path,
) -> None:
    store = SQLiteConfigurationStore(tmp_path / "home.sqlite3")
    invalid_candidate = {
        **VALID_CANDIDATE,
        "devices": [
            {
                **VALID_CANDIDATE["devices"][0],
                "room_id": "missing-room",
            }
        ],
    }

    try:
        with pytest.raises(ConfigurationValidationError):
            store.replace(expected_revision=0, candidate=invalid_candidate)

        assert store.read() == {
            "revision": 0,
            "rooms": [],
            "profiles": [],
            "wake_mappings": [],
            "devices": [],
        }
    finally:
        store.close()


def test_reopened_store_recovers_the_last_committed_configuration(tmp_path) -> None:
    database = tmp_path / "home.sqlite3"
    store = SQLiteConfigurationStore(database)
    committed = store.replace(expected_revision=0, candidate=VALID_CANDIDATE)
    store.close()

    reopened = SQLiteConfigurationStore(database)
    try:
        assert reopened.read() == committed
    finally:
        reopened.close()


def test_legacy_device_profile_snapshot_requires_trusted_publish(tmp_path) -> None:
    store = SQLiteConfigurationStore(tmp_path / "home.sqlite3")
    legacy = {
        "revision": 7,
        "rooms": [{"id": "kitchen", "name": "Kitchen"}],
        "mappings": [{"id": "amanda", "name": "Amanda"}],
        "devices": [
            {
                "id": "puck-kitchen",
                "name": "Kitchen Puck",
                "room_id": "kitchen",
                "profile_id": "amanda",
                "priority": 1,
                "capabilities": {"wake_claim": True},
            }
        ],
    }
    store._connection.execute(
        "UPDATE configuration SET revision = ?, snapshot = ? WHERE id = 1",
        (7, json.dumps(legacy)),
    )
    store._connection.commit()

    try:
        with pytest.raises(ConfigurationMigrationRequired) as raised:
            store.read()
        assert raised.value.current_revision == 7

        published = store.replace(expected_revision=7, candidate=VALID_CANDIDATE)
        assert published["revision"] == 8
        assert store.read() == published
    finally:
        store.close()


def test_expected_revision_must_be_a_non_negative_integer(tmp_path) -> None:
    store = SQLiteConfigurationStore(tmp_path / "home.sqlite3")

    try:
        with pytest.raises(ConfigurationValidationError):
            store.replace(expected_revision=True, candidate=VALID_CANDIDATE)
    finally:
        store.close()


def test_store_creates_the_database_parent_directory(tmp_path) -> None:
    database = tmp_path / "data" / "home.sqlite3"

    store = SQLiteConfigurationStore(database)

    try:
        assert database.exists()
    finally:
        store.close()


def test_concurrent_publishers_cannot_both_commit_the_same_revision(tmp_path) -> None:
    database = tmp_path / "home.sqlite3"
    first = SQLiteConfigurationStore(database)
    second = SQLiteConfigurationStore(database)
    barrier = threading.Barrier(2)
    committed = []
    conflicts = []

    def publish(store, candidate) -> None:
        barrier.wait(timeout=2)
        try:
            committed.append(store.replace(expected_revision=0, candidate=candidate))
        except RevisionConflict as conflict:
            conflicts.append(conflict)

    first_thread = threading.Thread(target=publish, args=(first, VALID_CANDIDATE))
    second_thread = threading.Thread(
        target=publish,
        args=(
            second,
            {
                **VALID_CANDIDATE,
                "rooms": [{"id": "hall", "name": "Hall"}],
                "devices": [
                    {
                        **VALID_CANDIDATE["devices"][0],
                        "room_id": "hall",
                    }
                ],
            },
        ),
    )
    first_thread.start()
    second_thread.start()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    try:
        assert len(committed) == 1
        assert len(conflicts) == 1
        assert conflicts[0].current_revision == 1
        assert first.read()["revision"] == 1
        assert second.read()["revision"] == 1
    finally:
        first.close()
        second.close()
