from __future__ import annotations

import itertools
import sqlite3

import pytest

from hermes_home.bridge.production import ConversationGrantStore
from hermes_home.domain.arbitration import WakeDecision
from hermes_home.domain.conversations import ConversationClaimConflict


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _configuration() -> dict[str, object]:
    return {
        "revision": 3,
        "rooms": [{"id": "kitchen", "name": "Kitchen"}],
        "profiles": [
            {"id": "amanda", "name": "Amanda", "available": True},
            {"id": "jensen", "name": "Jensen", "available": True},
        ],
        "wake_mappings": [
            {
                "id": "hey-hermes",
                "phrase": "Hey Hermes",
                "profile_id": "amanda",
                "active": True,
            }
        ],
        "devices": [],
    }


def _store(tmp_path, clock: Clock | None = None, **kwargs) -> ConversationGrantStore:
    handles = itertools.count(1)
    refs = itertools.count(1)
    return ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=_configuration,
        clock=clock or Clock(),
        handle_factory=lambda: f"handle-{next(handles)}",
        session_ref_factory=lambda: f"sref-{next(refs)}",
        **kwargs,
    )


def _claim(store: ConversationGrantStore, claim_id: str, **overrides) -> str:
    values = {
        "claim_id": claim_id,
        "device_id": "laptop",
        "grant_id": "grant-amanda",
        "profile_id": "amanda",
        "configuration_revision": 3,
        "credential_generation": 1,
    }
    values.update(overrides)
    return store.create_client_claim(**values)


def test_client_claim_resolves_to_its_profile_without_a_room(tmp_path) -> None:
    store = _store(tmp_path)

    handle = _claim(store, "claim-1")
    grant = store.resolve(handle, "laptop")

    assert grant is not None
    assert grant.profile_id == "amanda"
    assert grant.session_id is None


def test_client_claim_never_blocks_or_is_blocked_by_a_room_claim(tmp_path) -> None:
    store = _store(tmp_path)
    room_handle = store.create_from_decision(
        WakeDecision(
            claim_id="wake-1",
            decision="granted",
            arbitration_id="arb-1",
            configuration_revision=3,
            device_id="puck",
            room_id="kitchen",
            wake_mapping_id="hey-hermes",
            profile_id="amanda",
        ),
        credential_generation=1,
    )

    client_handle = _claim(store, "claim-1")

    assert store.resolve(room_handle, "puck") is not None
    assert store.resolve(client_handle, "laptop") is not None


def test_several_windows_share_the_device_limit(tmp_path) -> None:
    store = _store(tmp_path, client_claims_per_device=2)
    _claim(store, "claim-1")
    _claim(store, "claim-2")

    with pytest.raises(ConversationClaimConflict) as error:
        _claim(store, "claim-3")

    assert error.value.reason == "claim_limit"
    assert _claim(store, "claim-4", device_id="phone")


def test_closing_a_claim_frees_its_slot(tmp_path) -> None:
    store = _store(tmp_path, client_claims_per_device=1)
    handle = _claim(store, "claim-1")

    store.close_claim(handle, "laptop")

    assert _claim(store, "claim-2")


def test_pausing_never_ends_a_client_claim(tmp_path) -> None:
    clock = Clock()
    store = _store(tmp_path, clock)
    handle = _claim(store, "claim-1")
    store.mark_open(handle, "laptop")
    store.record_activity(handle, "laptop", "turn")
    store.record_activity(handle, "laptop", "playback")
    store.record_activity(handle, "laptop", "playback_complete")

    clock.now += 3_600

    assert store.resolve(handle, "laptop") is not None


def test_disconnect_starts_a_grace_that_reconnect_clears(tmp_path) -> None:
    clock = Clock()
    store = _store(tmp_path, clock, client_reconnect_grace_seconds=120)
    handle = _claim(store, "claim-1")
    store.mark_open(handle, "laptop")

    store.mark_disconnected(handle, "laptop")
    clock.now += 60
    store.mark_open(handle, "laptop")
    clock.now += 3_600

    assert store.resolve(handle, "laptop") is not None


def test_claim_closes_after_the_reconnect_grace(tmp_path) -> None:
    clock = Clock()
    store = _store(tmp_path, clock, client_reconnect_grace_seconds=120)
    handle = _claim(store, "claim-1")
    store.mark_open(handle, "laptop")

    store.mark_disconnected(handle, "laptop")
    clock.now += 121

    assert store.resolve(handle, "laptop") is None
    reason = store._connection.execute(
        "SELECT close_reason FROM conversation_claims WHERE handle = ?", (handle,)
    ).fetchone()[0]
    assert reason == "client_disconnected"


def test_resume_of_a_session_held_elsewhere_is_busy(tmp_path) -> None:
    store = _store(tmp_path)
    _claim(store, "claim-1", session_id="stored-1")

    with pytest.raises(ConversationClaimConflict) as error:
        _claim(store, "claim-2", session_id="stored-1")

    assert error.value.reason == "session_busy"
    assert store.active_session_ids() == frozenset({"stored-1"})


def test_resumed_claim_opens_the_chosen_session(tmp_path) -> None:
    store = _store(tmp_path)

    handle = _claim(store, "claim-1", session_id="stored-1")

    assert store.resolve(handle, "laptop").session_id == "stored-1"


def test_session_refs_are_stable_and_grant_scoped(tmp_path) -> None:
    store = _store(tmp_path)

    ref = store.session_ref("grant-amanda", "stored-1")

    assert store.session_ref("grant-amanda", "stored-1") == ref
    assert store.session_for_ref("grant-amanda", ref) == "stored-1"
    assert store.session_for_ref("grant-jensen", ref) is None
    assert store.session_ref("grant-jensen", "stored-1") != ref


def test_closing_a_grant_closes_its_claims(tmp_path) -> None:
    store = _store(tmp_path)
    first = _claim(store, "claim-1")
    other = _claim(store, "claim-2", grant_id="grant-jensen", profile_id="jensen")

    assert store.close_grant_claims("grant-amanda", reason="grant_revoked") == 1

    assert store.resolve(first, "laptop") is None
    assert store.resolve(other, "laptop") is not None


def test_pre_client_claim_database_is_migrated_in_place(tmp_path) -> None:
    path = tmp_path / "home.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.execute(
        """
        CREATE TABLE conversation_claims (
            handle TEXT PRIMARY KEY, claim_id TEXT NOT NULL UNIQUE,
            device_id TEXT NOT NULL, room_id TEXT NOT NULL,
            wake_mapping_id TEXT NOT NULL, profile_id TEXT NOT NULL,
            configuration_revision INTEGER NOT NULL, credential_generation INTEGER,
            session_id TEXT, status TEXT NOT NULL, activity TEXT NOT NULL,
            idle_deadline REAL, close_reason TEXT, created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    legacy.execute(
        "INSERT INTO conversation_claims VALUES ('old', 'old-claim', 'puck', "
        "'kitchen', 'hey-hermes', 'amanda', 3, 1, 'stored-0', 'closed', 'closed', "
        "NULL, 'stopped', 1.0, 1.0)"
    )
    legacy.commit()
    legacy.close()

    store = _store(tmp_path)
    handle = _claim(store, "claim-1")

    assert store.resolve(handle, "laptop") is not None
    kind = store._connection.execute(
        "SELECT claim_kind, room_id FROM conversation_claims WHERE handle = 'old'"
    ).fetchone()
    assert kind == ("room", "kitchen")
