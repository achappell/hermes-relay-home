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
    claim_refs = itertools.count(1)
    kwargs.setdefault("claim_ref_factory", lambda: f"cref-{next(claim_refs)}")
    kwargs.setdefault("handle_factory", lambda: f"handle-{next(handles)}")
    return ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=_configuration,
        clock=clock or Clock(),
        session_ref_factory=lambda: f"sref-{next(refs)}",
        **kwargs,
    )


def _claim_with_ref(
    store: ConversationGrantStore, claim_id: str, **overrides
) -> tuple[str, str]:
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


def _claim(store: ConversationGrantStore, claim_id: str, **overrides) -> str:
    return _claim_with_ref(store, claim_id, **overrides)[0]


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
    columns = {
        row[1]
        for row in store._connection.execute("PRAGMA table_info(conversation_claims)")
    }
    assert {"claim_ref", "created_wall_at", "opened_at"} <= columns
    index_sql = store._connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' "
        "AND name = 'conversation_claims_claim_ref'"
    ).fetchone()[0]
    assert index_sql.endswith("WHERE claim_ref IS NOT NULL")


# HOME-NW-18: list and close a device's own client claims.


class WallClock:
    def __init__(self) -> None:
        self.now = 1_727_398_000.4

    def __call__(self) -> float:
        return self.now


def _state_of(store: ConversationGrantStore, ref: str) -> str:
    return next(
        view.state for view in store.client_claims("laptop") if view.claim_ref == ref
    )


def _close_reason(store: ConversationGrantStore, handle: str) -> str:
    return store._connection.execute(
        "SELECT close_reason FROM conversation_claims WHERE handle = ?", (handle,)
    ).fetchone()[0]


def test_every_active_activity_maps_to_a_list_state(tmp_path) -> None:
    store = _store(tmp_path, client_claims_per_device=16)
    expected = {
        "ready": "connecting",
        "open": "idle",
        "response_ready": "idle",
        "idle": "idle",
        "turn": "replying",
        "capture": "replying",
        "playback": "replying",
        "disconnected": "waiting_to_reconnect",
    }
    refs = {}
    for activity in expected:
        handle, ref = _claim_with_ref(store, f"claim-{activity}")
        refs[activity] = ref
        if activity == "ready":
            continue
        store.mark_open(handle, "laptop")
        if activity == "disconnected":
            store.mark_disconnected(handle, "laptop")
        elif activity == "response_ready":
            store.record_activity(handle, "laptop", "turn")
            store.record_activity(handle, "laptop", "response_ready")
        elif activity != "open":
            store.record_activity(handle, "laptop", activity)

    stored = dict(
        store._connection.execute(
            "SELECT claim_ref, activity FROM conversation_claims WHERE status = 'active'"
        ).fetchall()
    )
    assert {activity: stored[ref] for activity, ref in refs.items()} == {
        activity: activity for activity in expected
    }
    assert {
        activity: _state_of(store, ref) for activity, ref in refs.items()
    } == expected


def test_a_parked_claim_waits_to_reconnect_whatever_its_activity(tmp_path) -> None:
    store = _store(tmp_path)
    handle, ref = _claim_with_ref(store, "claim-1")
    store.mark_open(handle, "laptop")
    store.record_activity(handle, "laptop", "turn")

    store.mark_detached(handle)
    assert _state_of(store, ref) == "waiting_to_reconnect"

    # Park expiry clears the marker and the closing bridge starts the grace.
    store.clear_detached(handle)
    store.mark_disconnected(handle, "laptop")
    assert _state_of(store, ref) == "waiting_to_reconnect"

    # Adoption or reconnect reopens the claim and clears the marker.
    store.mark_detached(handle)
    store.mark_open(handle, "laptop")
    assert _state_of(store, ref) == "idle"


def test_detached_marker_overrides_every_active_activity(tmp_path) -> None:
    store = _store(tmp_path, client_claims_per_device=16)
    expected = {
        "ready": "connecting",
        "open": "idle",
        "response_ready": "idle",
        "idle": "idle",
        "turn": "replying",
        "capture": "replying",
        "playback": "replying",
        "disconnected": "waiting_to_reconnect",
    }
    for activity in expected:
        handle, ref = _claim_with_ref(store, f"detached-{activity}")
        store._connection.execute(
            "UPDATE conversation_claims SET activity = ? WHERE handle = ?",
            (activity, handle),
        )
        store._connection.commit()
        store.mark_detached(handle)
        assert _state_of(store, ref) == "waiting_to_reconnect"
    store.close()


def test_mark_open_rejects_inactive_and_expired_claims(tmp_path) -> None:
    clock = Clock()
    store = _store(tmp_path, clock)
    inactive_handle, inactive_ref = _claim_with_ref(store, "inactive")
    store.close_client_claims("laptop", [inactive_ref])
    with pytest.raises(ValueError, match="no longer active"):
        store.mark_open(inactive_handle, "laptop")
    assert _close_reason(store, inactive_handle) == "client_closed"

    expired_handle, _expired_ref = _claim_with_ref(store, "expired")
    clock.now += 91
    with pytest.raises(ValueError, match="expired"):
        store.mark_open(expired_handle, "laptop")
    assert _close_reason(store, expired_handle) == "first_open_expired"
    store.close()


def test_detached_marker_never_sets_a_deadline(tmp_path) -> None:
    clock = Clock()
    store = _store(tmp_path, clock)
    handle, _ref = _claim_with_ref(store, "claim-1")
    store.mark_open(handle, "laptop")

    store.mark_detached(handle)
    clock.now += 10_000

    assert store.resolve(handle, "laptop") is not None


def test_list_times_are_wall_clock_and_opened_at_is_set_once(tmp_path) -> None:
    wall = WallClock()
    store = _store(tmp_path, wall_clock=wall)
    handle, _ref = _claim_with_ref(store, "claim-1")

    (view,) = store.client_claims("laptop")
    assert (view.created_at, view.opened_at, view.state) == (
        1_727_398_000.4,
        None,
        "connecting",
    )

    wall.now += 5
    store.mark_open(handle, "laptop")
    store.mark_disconnected(handle, "laptop")
    wall.now += 30
    store.mark_open(handle, "laptop")

    (view,) = store.client_claims("laptop")
    assert view.opened_at == 1_727_398_005.4


def test_list_is_device_scoped_and_excludes_room_claims(tmp_path) -> None:
    store = _store(tmp_path)
    store.create_from_decision(
        WakeDecision(
            claim_id="wake-1",
            decision="granted",
            arbitration_id="arb-1",
            configuration_revision=3,
            device_id="laptop",
            room_id="kitchen",
            wake_mapping_id="hey-hermes",
            profile_id="amanda",
        ),
        credential_generation=1,
    )
    _own, own_ref = _claim_with_ref(store, "claim-1")
    _claim_with_ref(store, "claim-2", device_id="phone")

    assert [view.claim_ref for view in store.client_claims("laptop")] == [own_ref]


def test_wake_and_touch_room_claims_are_not_listed_or_closed_by_refs(tmp_path) -> None:
    store = _store(tmp_path, client_claims_per_device=8)
    wake = WakeDecision(
        claim_id="wake-1",
        decision="granted",
        arbitration_id="arb-wake",
        configuration_revision=3,
        device_id="laptop",
        room_id="kitchen",
        wake_mapping_id="hey-hermes",
        profile_id="amanda",
    )
    touch = WakeDecision(
        claim_id="touch-1",
        decision="granted",
        arbitration_id="arb-touch",
        configuration_revision=3,
        device_id="laptop",
        room_id="bedroom",
        wake_mapping_id="hey-hermes",
        profile_id="amanda",
        claim_kind="touch",
    )
    wake_handle = store.create_from_decision(wake, credential_generation=1)
    touch_handle = store.create_from_decision(touch, credential_generation=1)

    store._connection.execute(
        "UPDATE conversation_claims SET claim_ref = CASE handle "
        "WHEN ? THEN 'cref-wake' ELSE 'cref-touch' END "
        "WHERE handle IN (?, ?)",
        (wake_handle, wake_handle, touch_handle),
    )
    store._connection.commit()
    assert store.client_claims("laptop") == []
    assert (
        store.close_client_claims("laptop", ["cref-wake", "cref-touch"]) == frozenset()
    )
    assert store.resolve(wake_handle, "laptop") is not None
    assert store.resolve(touch_handle, "laptop") is not None
    store.close()


def test_list_is_newest_first_and_counts_what_the_limit_counts(tmp_path) -> None:
    clock = Clock()
    wall = WallClock()
    store = _store(tmp_path, clock, client_claims_per_device=2, wall_clock=wall)
    _first, first_ref = _claim_with_ref(store, "claim-1")
    wall.now += 1
    _second, second_ref = _claim_with_ref(store, "claim-2")

    assert [view.claim_ref for view in store.client_claims("laptop")] == [
        second_ref,
        first_ref,
    ]

    # Both are past their first-open deadline: the list sweeps them just as
    # create does, so the device has room again.
    clock.now += 91
    assert store.client_claims("laptop") == []
    assert _claim(store, "claim-3")


def test_closing_unopened_claims_frees_slots_at_once(tmp_path) -> None:
    store = _store(tmp_path, client_claims_per_device=1)
    handle, ref = _claim_with_ref(store, "claim-1")
    assert handle in store._timers

    assert store.close_client_claims("laptop", [ref]) == frozenset({ref})

    assert handle not in store._timers
    assert _close_reason(store, handle) == "client_closed"
    assert _claim(store, "claim-2")


def test_close_answers_only_for_the_callers_active_client_claims(tmp_path) -> None:
    clock = Clock()
    store = _store(tmp_path, clock, client_claims_per_device=8)
    _closed_handle, closed_ref = _claim_with_ref(store, "claim-closed")
    store.close_client_claims("laptop", [closed_ref])
    _expired_handle, expired_ref = _claim_with_ref(store, "claim-expired")
    clock.now += 91
    other_handle, other_ref = _claim_with_ref(store, "claim-other", device_id="phone")
    live_handle, live_ref = _claim_with_ref(store, "claim-live")

    closed = store.close_client_claims(
        "laptop", [other_ref, closed_ref, expired_ref, "cref-random", live_ref]
    )

    assert closed == frozenset({live_ref})
    assert store.resolve(other_handle, "phone") is not None
    assert _close_reason(store, live_handle) == "client_closed"


def test_first_close_reason_is_kept(tmp_path) -> None:
    store = _store(tmp_path)
    handle, ref = _claim_with_ref(store, "claim-1")
    store.close_client_claims("laptop", [ref])

    assert store.close_claim(handle, "laptop", reason="upstream_lost") is True
    assert store.close_claim(handle, "laptop", reason="session_startup_failed")

    assert _close_reason(store, handle) == "client_closed"


def test_close_notifies_handlers_and_close_listeners_outside_the_lock(tmp_path) -> None:
    store = _store(tmp_path)
    handle, ref = _claim_with_ref(store, "claim-1")
    store.mark_open(handle, "laptop")
    seen = []

    def handler(reason: str) -> None:
        # Would deadlock if the store still held its lock on another thread.
        assert store._lock.acquire(blocking=False)
        store._lock.release()
        seen.append(("handler", reason))

    store.register_revocation_handler(handle, handler)
    store.add_close_listener(lambda closed: seen.append(("listener", closed)))
    store.mark_detached(handle)

    store.close_client_claims("laptop", [ref])

    assert seen == [("handler", "client_closed"), ("listener", handle)]
    assert handle not in store._detached


def test_concurrent_close_and_create_never_exceed_the_limit(tmp_path) -> None:
    import threading

    for round_index in range(10):
        store = _store(tmp_path / f"round-{round_index}", client_claims_per_device=1)
        _handle, ref = _claim_with_ref(store, "claim-1")
        barrier = threading.Barrier(2)
        outcome: dict[str, object] = {}

        def close(store=store, ref=ref, barrier=barrier, outcome=outcome) -> None:
            barrier.wait()
            outcome["closed"] = store.close_client_claims("laptop", [ref])

        def create(store=store, barrier=barrier, outcome=outcome) -> None:
            barrier.wait()
            try:
                outcome["created"] = _claim(store, "claim-2")
            except ConversationClaimConflict as error:
                outcome["created"] = error.reason

        threads = [threading.Thread(target=close), threading.Thread(target=create)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert outcome["closed"] == frozenset({ref})
        assert outcome["created"] in {"handle-2", "claim_limit"}
        assert len(store.client_claims("laptop")) <= 1
        store.close()


def test_nw17_database_gains_claim_refs_in_place(tmp_path) -> None:
    path = tmp_path / "home.sqlite3"
    first = _store(tmp_path)
    old_handle = _claim(first, "claim-old")
    first._connection.execute(
        "UPDATE conversation_claims SET claim_ref = NULL, created_wall_at = NULL"
    )
    first._connection.commit()
    first.close()
    # Rebuild the NW-17 shape: no claim_ref, created_wall_at or opened_at.
    legacy = sqlite3.connect(path)
    legacy.execute("DROP INDEX IF EXISTS conversation_claims_claim_ref")
    for column in ("claim_ref", "created_wall_at", "opened_at"):
        legacy.execute(f"ALTER TABLE conversation_claims DROP COLUMN {column}")
    legacy.commit()
    legacy.close()

    store = _store(tmp_path, handle_factory=lambda: "handle-new")
    row = store._connection.execute(
        "SELECT status, close_reason, claim_ref FROM conversation_claims "
        "WHERE handle = ?",
        (old_handle,),
    ).fetchone()
    assert row == ("closed", "service_restart", None)
    assert store.client_claims("laptop") == []
    _handle, ref = _claim_with_ref(store, "claim-new")
    assert [view.claim_ref for view in store.client_claims("laptop")] == [ref]
    store.close()

    again = _store(tmp_path, handle_factory=lambda: "handle-again")
    columns = [
        row[1]
        for row in again._connection.execute("PRAGMA table_info(conversation_claims)")
    ]
    assert columns.count("claim_ref") == 1
    assert {"claim_ref", "created_wall_at", "opened_at"} <= set(columns)
    index = again._connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' "
        "AND name = 'conversation_claims_claim_ref'"
    ).fetchone()
    assert index is not None
    assert index[0].endswith("WHERE claim_ref IS NOT NULL")
    assert again._connection.execute(
        "SELECT status, close_reason, claim_ref FROM conversation_claims "
        "WHERE handle = ?",
        (old_handle,),
    ).fetchone() == ("closed", "service_restart", None)
    assert again._connection.execute(
        "SELECT status, close_reason FROM conversation_claims WHERE handle = ?",
        (_handle,),
    ).fetchone() == ("closed", "service_restart")
