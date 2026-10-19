from __future__ import annotations

import itertools

import pytest

from hermes_home.bridge.production import ConversationGrantStore
from hermes_home.domain.arbitration import ArbitrationEngine, WakeDecision
from hermes_home.domain.conversations import (
    ConversationClaimConflict,
    InMemoryConversationClaimStore,
)
from tests.test_client_claim_close_bridge import (
    HEADERS,
    BlockingJsonSocket,
    ContextAuthenticator,
    SocketFactory,
    _bridge,
    _standard_frames,
)


def _configuration() -> dict[str, object]:
    return {
        "revision": 3,
        "rooms": [
            {"id": "living-room", "name": "Living Room"},
            {"id": "office", "name": "Office"},
        ],
        "profiles": [{"id": "amanda", "name": "Amanda", "available": True}],
        "wake_mappings": [
            {
                "id": "hey-missy",
                "phrase": "Hey Missy",
                "profile_id": "amanda",
                "active": True,
            }
        ],
        "devices": [
            {
                "id": "puck",
                "name": "Living Room Puck",
                "room_id": "living-room",
                "priority": 1,
                "capabilities": {"wake_claim": True},
                "wake_admission": {
                    "mode": "paired_room",
                    "endpoint_id": "puck-bridge",
                    "wake_mapping_ids": ["hey-missy"],
                },
            }
        ],
    }


def _decision(
    claim_id: str = "paired-claim",
    *,
    kind: str = "paired_room",
    room_id: str = "living-room",
    revision: int = 3,
) -> WakeDecision:
    return WakeDecision(
        claim_id=claim_id,
        decision="granted",
        arbitration_id=f"admission-{claim_id}",
        configuration_revision=revision,
        device_id="puck",
        room_id=room_id,
        wake_mapping_id="hey-missy",
        profile_id="amanda",
        claim_kind=kind,
    )


def _allow_paired(device_id, generation, revision, mapping_id) -> bool:
    return (device_id, generation, revision, mapping_id) == (
        "puck",
        2,
        3,
        "hey-missy",
    )


@pytest.fixture
def store(tmp_path):
    handles = itertools.count(1)
    claims = ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=_configuration,
        clock=lambda: 1_000.0,
        handle_factory=lambda: f"conversation-{next(handles)}",
    )
    yield claims
    claims.close()


def _row(store: ConversationGrantStore, handle: str):
    return store._connection.execute(
        "SELECT claim_kind, status, close_reason FROM conversation_claims "
        "WHERE handle = ?",
        (handle,),
    ).fetchone()


def _create(store: ConversationGrantStore, claim_id: str, kind: str) -> str:
    if kind == "client":
        return store.create_client_claim(
            claim_id=claim_id,
            device_id="puck",
            grant_id="personal-amanda",
            profile_id="amanda",
            configuration_revision=3,
            credential_generation=2,
        )[0]
    return store.create_from_decision(
        _decision(claim_id, kind=kind), credential_generation=2
    )


def test_paired_kind_and_binding_survive_reopen_without_replaying_claim(
    tmp_path,
) -> None:
    path = tmp_path / "home.sqlite3"
    store = ConversationGrantStore(path, configuration=_configuration)
    store.set_paired_room_checker(_allow_paired)
    try:
        handle = store.create_from_decision(_decision(), credential_generation=2)
        assert store.resolve(handle, "different-device") is None
        grant = store.resolve(handle, "puck")
        assert grant is not None
        assert grant.credential_generation == 2
        assert grant.configuration_revision == 3
        assert grant.profile_id == "amanda"
        store.persist_session(grant, "standard-session")
        store.mark_open(handle, "puck")
        assert store.resolve(handle, "puck").session_id == "standard-session"
        row = store._connection.execute(
            "SELECT claim_kind, claim_id, device_id, room_id, wake_mapping_id, "
            "profile_id, configuration_revision, credential_generation, session_id "
            "FROM conversation_claims WHERE handle = ?",
            (handle,),
        ).fetchone()
        assert row == (
            "paired_room",
            "paired-claim",
            "puck",
            "living-room",
            "hey-missy",
            "amanda",
            3,
            2,
            "standard-session",
        )
    finally:
        store.close()

    reopened = ConversationGrantStore(path, configuration=_configuration)
    reopened.set_paired_room_checker(_allow_paired)
    try:
        assert _row(reopened, handle) == (
            "paired_room",
            "closed",
            "service_restart",
        )
        assert reopened.resolve(handle, "puck") is None
        with pytest.raises(ConversationClaimConflict) as error:
            reopened.create_from_decision(_decision(), credential_generation=2)
        assert error.value.reason == "duplicate_claim"
        fresh = reopened.create_from_decision(
            _decision("fresh-claim"), credential_generation=2
        )
        assert fresh != handle
        assert reopened.resolve(fresh, "puck") is not None
        assert reopened.resolve(handle, "puck") is None
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "failure",
    [None, False, OSError, RuntimeError, TypeError, ValueError, KeyError, Exception],
)
def test_paired_resolution_retires_missing_denied_or_failed_checker(
    store, failure
) -> None:
    if failure is False:
        store.set_paired_room_checker(lambda *_args: False)
    elif failure is not None:

        def unavailable(*_args):
            raise failure("authority unavailable")

        store.set_paired_room_checker(unavailable)
    handle = store.create_from_decision(_decision(), credential_generation=2)

    assert store.resolve(handle, "puck") is None
    assert _row(store, handle) == ("paired_room", "closed", "paired_room_revoked")
    store.set_paired_room_checker(_allow_paired)
    assert store.resolve(handle, "puck") is None
    with pytest.raises(ConversationClaimConflict) as error:
        store.create_from_decision(_decision(), credential_generation=2)
    assert error.value.reason == "duplicate_claim"
    fresh = store.create_from_decision(
        _decision("fresh-claim"), credential_generation=2
    )
    assert fresh != handle
    assert store.resolve(fresh, "puck") is not None


def test_competitor_activation_rechecks_same_revision_without_reusing_authority(
    store,
) -> None:
    competitor_active = False
    checked = []

    def checker(device_id, generation, revision, mapping_id):
        checked.append((device_id, generation, revision, mapping_id))
        return not competitor_active and _allow_paired(
            device_id, generation, revision, mapping_id
        )

    store.set_paired_room_checker(checker)
    handle = store.create_from_decision(_decision(), credential_generation=2)
    assert store.resolve(handle, "puck") is not None
    assert store.resolve(handle, "puck") is not None
    competitor_active = True
    assert store.resolve(handle, "puck") is None
    assert checked == [("puck", 2, 3, "hey-missy")] * 3
    assert _row(store, handle) == ("paired_room", "closed", "paired_room_revoked")
    competitor_active = False
    assert store.resolve(handle, "puck") is None
    assert len(checked) == 3


def test_paired_resolution_checks_stored_revision_against_current_authority(
    store,
) -> None:
    current_revision = 3

    def checker(device_id, generation, revision, mapping_id):
        return revision == current_revision and _allow_paired(
            device_id, generation, revision, mapping_id
        )

    store.set_paired_room_checker(checker)
    handle = store.create_from_decision(_decision(), credential_generation=2)
    assert store.resolve(handle, "puck") is not None
    current_revision = 4
    assert store.resolve(handle, "puck") is None
    current_revision = 3
    assert store.resolve(handle, "puck") is None


@pytest.mark.parametrize(
    ("existing_kind", "new_kind"),
    [("wake", "paired_room"), ("touch", "paired_room"), ("paired_room", "wake")],
)
@pytest.mark.parametrize("idle", [False, True])
def test_paired_and_strict_claims_share_room_busy_conflict(
    store, existing_kind, new_kind, idle
) -> None:
    store.set_paired_room_checker(_allow_paired)
    existing = _create(store, "existing", existing_kind)
    if idle:
        store.mark_open(existing, "puck")
        store.record_activity(existing, "puck", "idle")

    with pytest.raises(ConversationClaimConflict) as error:
        _create(store, "next", new_kind)

    assert error.value.reason == "conversation_active"
    assert store.resolve(existing, "puck") is not None
    assert _row(store, existing)[1:] == ("active", None)


@pytest.mark.parametrize(
    ("existing_kind", "new_kind"),
    [
        ("wake", "paired_room"),
        ("touch", "paired_room"),
        ("client", "paired_room"),
        ("paired_room", "wake"),
        ("paired_room", "touch"),
        ("paired_room", "client"),
        ("paired_room", "paired_room"),
    ],
)
def test_claim_ids_are_single_use_across_paired_strict_and_client_kinds(
    store, existing_kind, new_kind
) -> None:
    existing = _create(store, "shared-claim", existing_kind)
    assert store.close_claim(existing, "puck")

    with pytest.raises(ConversationClaimConflict) as error:
        _create(store, "shared-claim", new_kind)

    assert error.value.reason == "duplicate_claim"
    assert store.resolve(existing, "puck") is None


@pytest.mark.parametrize("kind", ["wake", "touch", "client"])
def test_strict_touch_and_client_resolution_do_not_use_paired_checker(
    store, kind
) -> None:
    calls = []

    def forbidden_checker(*args):
        calls.append(args)
        raise AssertionError("strict claims must not check paired authority")

    store.set_paired_room_checker(forbidden_checker)
    handle = _create(store, "existing-claim", kind)
    assert store.resolve(handle, "puck") is not None
    assert calls == []
    assert _row(store, handle) == (
        "client" if kind == "client" else "room",
        "active",
        None,
    )


@pytest.mark.parametrize("strict_kind", ["wake", "touch", "client"])
def test_revision_retirement_only_closes_older_paired_claims_and_notifies(
    store, strict_kind
) -> None:
    old_paired = store.create_from_decision(_decision(), credential_generation=2)
    strict = (
        _create(store, "strict-claim", "client")
        if strict_kind == "client"
        else store.create_from_decision(
            _decision("strict-claim", kind=strict_kind, room_id="office"),
            credential_generation=2,
        )
    )
    revocations = []
    closed = []
    assert store.register_revocation_handler(old_paired, revocations.append)
    store.add_close_listener(closed.append)

    assert (
        store.close_paired_room_claims(
            configuration_revision=3, reason="configuration_changed"
        )
        == 0
    )
    assert (
        store.close_paired_room_claims(
            configuration_revision=4, reason="configuration_changed"
        )
        == 1
    )
    assert revocations == ["configuration_changed"]
    assert closed == [old_paired]
    assert _row(store, old_paired) == (
        "paired_room",
        "closed",
        "configuration_changed",
    )
    assert store.resolve(strict, "puck") is not None
    current_paired = store.create_from_decision(
        _decision("current-claim", revision=4), credential_generation=2
    )
    assert (
        store.close_paired_room_claims(
            configuration_revision=4, reason="configuration_changed"
        )
        == 0
    )
    assert _row(store, current_paired) == ("paired_room", "active", None)
    assert closed == [old_paired]


def test_in_memory_store_records_paired_metadata_for_selective_retirement() -> None:
    handles = itertools.count(1)
    store = InMemoryConversationClaimStore(lambda: f"conversation-{next(handles)}")
    store.set_paired_room_checker(_allow_paired)
    paired = store.create_from_decision(_decision(), credential_generation=2)
    strict = store.create_from_decision(
        _decision("strict-claim", kind="wake", room_id="office"),
        credential_generation=2,
    )

    assert store._claims[paired]["claim_kind"] == "paired_room"
    assert store._claims[paired]["configuration_revision"] == 3
    assert store._claims[paired]["credential_generation"] == 2
    assert store._claims[paired]["wake_mapping_id"] == "hey-missy"
    assert (
        store.close_paired_room_claims(
            configuration_revision=4, reason="configuration_changed"
        )
        == 1
    )
    assert store._claims[paired]["status"] == "closed"
    assert store._claims[strict]["status"] == "active"
    with pytest.raises(ConversationClaimConflict) as error:
        store.create_from_decision(_decision(), credential_generation=2)
    assert error.value.reason == "duplicate_claim"


@pytest.mark.parametrize("first_kind", ["paired_room", "wake"])
def test_paired_reservation_and_pending_strict_admission_share_single_use_ids(
    first_kind,
) -> None:
    engine = ArbitrationEngine(configuration=_configuration, clock=lambda: 0.0)
    strict_claim = {
        "schema": 1,
        "claim_id": "shared-claim",
        "device_id": "puck",
        "wake_mapping_id": "hey-missy",
        "configuration_revision": 3,
        "observation": {"detector": "device-local"},
        "acoustic_evidence": {"kind": "opaque-v1", "value": 0.91},
        "availability": "ready",
    }
    if first_kind == "paired_room":
        assert engine.reserve_claim_id("shared-claim") is True
        submission = engine.submit(strict_claim, authenticated_device_id="puck")
        assert submission.accepted is False
        assert submission.reason == "duplicate_claim"
        assert engine.finalize(now=1.0) is None
    else:
        submission = engine.submit(strict_claim, authenticated_device_id="puck")
        assert submission.accepted is True
        assert engine.reserve_claim_id("shared-claim") is False
        assert engine.finalize() is None
        decisions = engine.finalize(now=1.0)
        assert decisions["shared-claim"].decision == "granted"
    assert engine.reserve_claim_id("shared-claim") is False


def test_checker_denial_cannot_readmit_handle_when_retirement_storage_fails(
    store, monkeypatch
) -> None:
    store.set_paired_room_checker(lambda *_args: False)
    handle = store.create_from_decision(_decision(), credential_generation=2)
    revocations = []
    assert store.register_revocation_handler(handle, revocations.append)

    def unavailable(*_args):
        raise OSError("claim storage unavailable")

    with monkeypatch.context() as patched:
        patched.setattr(store, "_close_matching_rows", unavailable)
        with pytest.raises(OSError, match="claim storage unavailable"):
            store.resolve(handle, "puck")

    assert _row(store, handle) == ("paired_room", "active", None)
    assert revocations == []
    store.set_paired_room_checker(_allow_paired)
    assert store.resolve(handle, "puck") is None
    assert revocations == ["paired_room_revoked"]
    assert store.close_claim(handle, "puck")
    with pytest.raises(ConversationClaimConflict) as error:
        store.create_from_decision(_decision(), credential_generation=2)
    assert error.value.reason == "duplicate_claim"


def test_configuration_publish_racing_checker_retires_paired_claim(tmp_path) -> None:
    configuration = _configuration()
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3", configuration=lambda: configuration
    )

    def publish_after_authorization(*args):
        allowed = _allow_paired(*args)
        configuration["revision"] = 4
        return allowed

    store.set_paired_room_checker(publish_after_authorization)
    try:
        handle = store.create_from_decision(_decision(), credential_generation=2)
        assert store.resolve(handle, "puck") is None
        assert _row(store, handle) == ("paired_room", "closed", "paired_room_revoked")
    finally:
        store.close()


@pytest.mark.parametrize("kind", ["wake", "touch", "client"])
def test_configuration_revision_change_does_not_pin_existing_strict_claims(
    tmp_path, kind
) -> None:
    configuration = _configuration()
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3", configuration=lambda: configuration
    )
    try:
        handle = _create(store, "strict-claim", kind)
        configuration["revision"] = 4
        assert store.resolve(handle, "puck") is not None
        assert _row(store, handle)[1:] == ("active", None)
    finally:
        store.close()


@pytest.mark.parametrize(
    ("phase", "reason"),
    [("ready", "first_open_expired"), ("idle", "idle_expired")],
)
def test_paired_claim_keeps_existing_first_open_and_idle_deadlines(
    tmp_path, phase, reason
) -> None:
    now = [1_000.0]
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3",
        configuration=_configuration,
        clock=lambda: now[0],
        first_open_timeout_seconds=5.0,
        idle_timeout_seconds=2.0,
    )
    store.set_paired_room_checker(_allow_paired)
    try:
        handle = store.create_from_decision(_decision(), credential_generation=2)
        if phase == "idle":
            store.mark_open(handle, "puck")
            store.record_activity(handle, "puck", "idle")
            now[0] += 2.0
        else:
            now[0] += 5.0
        assert store.resolve(handle, "puck") is None
        assert _row(store, handle) == ("paired_room", "closed", reason)
        with pytest.raises(ConversationClaimConflict) as error:
            store.create_from_decision(_decision(), credential_generation=2)
        assert error.value.reason == "duplicate_claim"
    finally:
        store.close()


def test_owned_paired_close_stops_and_notifies_once_without_policy_check(store) -> None:
    handle = store.create_from_decision(_decision(), credential_generation=2)
    revocations = []
    closed = []
    assert store.register_revocation_handler(handle, revocations.append)
    store.add_close_listener(closed.append)

    for _ in range(2):
        assert store.close_owned_paired_room_claim(
            handle,
            "puck",
            authorized_rooms=("living-room",),
            authorized_wake_mappings=("hey-missy",),
        )
        assert _row(store, handle) == ("paired_room", "closed", "stopped")
    assert revocations == ["stopped"]
    assert closed == [handle]
    assert store.resolve(handle, "puck") is None


@pytest.mark.parametrize("already_closed", [False, True])
@pytest.mark.parametrize(
    ("kind", "device_id", "unknown", "rooms", "mappings"),
    [
        ("paired_room", "puck", True, ("living-room",), ("hey-missy",)),
        ("paired_room", "other-device", False, ("living-room",), ("hey-missy",)),
        ("paired_room", "puck", False, ("office",), ("hey-missy",)),
        ("paired_room", "puck", False, ("living-room",), ("other-mapping",)),
        ("paired_room", "puck", False, (), ("hey-missy",)),
        ("paired_room", "puck", False, ("living-room",), ()),
        ("wake", "puck", False, ("living-room",), ("hey-missy",)),
        ("touch", "puck", False, ("living-room",), ("hey-missy",)),
        ("client", "puck", False, ("living-room",), ("hey-missy",)),
    ],
)
def test_owned_paired_close_rejects_unknown_foreign_strict_and_out_of_scope(
    store, already_closed, kind, device_id, unknown, rooms, mappings
) -> None:
    handle = _create(store, "existing-claim", kind)
    if already_closed:
        assert store.close_claim(handle, "puck", reason="configuration_changed")
    before = _row(store, handle)

    assert not store.close_owned_paired_room_claim(
        "unknown-handle" if unknown else handle,
        device_id,
        authorized_rooms=iter(rooms),
        authorized_wake_mappings=iter(mappings),
    )
    assert _row(store, handle) == before


def test_owned_paired_close_after_restart_needs_no_live_policy_or_generation(
    tmp_path,
) -> None:
    path = tmp_path / "home.sqlite3"
    store = ConversationGrantStore(path, configuration=_configuration)
    try:
        handle = store.create_from_decision(_decision(), credential_generation=2)
    finally:
        store.close()

    def unavailable_configuration():
        raise AssertionError("cold recovery must not require current paired policy")

    reopened = ConversationGrantStore(path, configuration=unavailable_configuration)
    try:
        assert reopened.close_owned_paired_room_claim(
            handle,
            "puck",
            authorized_rooms=("living-room",),
            authorized_wake_mappings=("hey-missy",),
        )
        assert _row(reopened, handle) == (
            "paired_room",
            "closed",
            "service_restart",
        )
        assert reopened.resolve(handle, "puck") is None
        with pytest.raises(ConversationClaimConflict) as error:
            reopened.create_from_decision(_decision(), credential_generation=3)
        assert error.value.reason == "duplicate_claim"
    finally:
        reopened.close()


def test_in_memory_owned_paired_close_preserves_scope_and_closed_idempotence() -> None:
    handles = itertools.count(1)
    store = InMemoryConversationClaimStore(lambda: f"conversation-{next(handles)}")
    paired = store.create_from_decision(_decision(), credential_generation=2)
    strict = store.create_from_decision(
        _decision("strict-claim", kind="wake", room_id="office"),
        credential_generation=2,
    )
    for handle, device_id, rooms, mappings in (
        ("unknown", "puck", ("living-room",), ("hey-missy",)),
        (paired, "other-device", ("living-room",), ("hey-missy",)),
        (paired, "puck", ("office",), ("hey-missy",)),
        (paired, "puck", ("living-room",), ("other-mapping",)),
        (strict, "puck", ("office",), ("hey-missy",)),
    ):
        assert not store.close_owned_paired_room_claim(
            handle,
            device_id,
            authorized_rooms=rooms,
            authorized_wake_mappings=mappings,
        )
    assert store._claims[paired]["status"] == "active"
    assert store._claims[strict]["status"] == "active"
    assert store.close_claim(paired, "puck", reason="configuration_changed")
    assert store.close_owned_paired_room_claim(
        paired,
        "puck",
        authorized_rooms=("living-room",),
        authorized_wake_mappings=("hey-missy",),
    )
    assert store._claims[paired]["close_reason"] == "configuration_changed"
    assert store._claims[strict]["status"] == "active"
    fresh = store.create_from_decision(
        _decision("fresh-paired-claim"), credential_generation=3
    )
    assert store.close_owned_paired_room_claim(
        fresh,
        "puck",
        authorized_rooms=("living-room",),
        authorized_wake_mappings=("hey-missy",),
    )
    assert store._claims[fresh]["status"] == "closed"
    assert store._claims[fresh]["close_reason"] == "stopped"


@pytest.mark.parametrize(
    "invalidation",
    ["same_revision_competitor", "checker_revision", "snapshot_revision"],
)
def test_paired_resolution_revokes_live_standard_binding_and_notifies_once(
    tmp_path, invalidation
) -> None:
    configuration = _configuration()
    authority = {"competitor_active": False, "revision": 3}
    store = ConversationGrantStore(
        tmp_path / "home.sqlite3", configuration=lambda: configuration
    )

    def checker(device_id, generation, revision, mapping_id):
        return (
            not authority["competitor_active"]
            and revision == authority["revision"]
            and _allow_paired(device_id, generation, revision, mapping_id)
        )

    store.set_paired_room_checker(checker)
    handle = store.create_from_decision(_decision(), credential_generation=2)
    socket = BlockingJsonSocket(_standard_frames())
    bridge = _bridge(
        store,
        SocketFactory(socket),
        authenticator=ContextAuthenticator("puck", generation=2),
    )
    revocations = []
    lock_owned = []
    closed = []

    def record_revocation(reason):
        revocations.append(reason)
        lock_owned.append(store._lock._is_owned())

    try:
        assert (
            bridge.open(headers=HEADERS, conversation_handle=handle).status == "ready"
        )
        bridge.submit_prompt("continue the authorized turn")
        assert store.register_revocation_handler(handle, record_revocation)
        store.add_close_listener(closed.append)
        if invalidation == "same_revision_competitor":
            authority["competitor_active"] = True
            assert configuration["revision"] == 3
        elif invalidation == "checker_revision":
            authority["revision"] = 4
        else:
            configuration["revision"] = 4

        status = bridge.reauthorize(headers=HEADERS)

        assert (status.status, status.reason) == ("unavailable", "stale_conversation")
        interrupts = [
            frame for frame in socket.sent if frame.get("method") == "session.interrupt"
        ]
        assert len(interrupts) == 1
        assert interrupts[0]["params"] == {"session_id": "runtime-hermes-1"}
        assert socket.closed
        assert bridge.state == "unavailable"
        assert bridge._gateway is None
        assert bridge._grant is None
        assert bridge._runtime_session_id is None
        assert bridge._active_turn is None
        assert store.active_session_ids() == frozenset()
        assert _row(store, handle) == ("paired_room", "closed", "paired_room_revoked")
        assert revocations == ["paired_room_revoked"]
        assert lock_owned == [False]
        assert closed == [handle]

        assert store.close_owned_paired_room_claim(
            handle,
            "puck",
            authorized_rooms=("living-room",),
            authorized_wake_mappings=("hey-missy",),
        )
        assert store.resolve(handle, "puck") is None
        assert bridge.reauthorize(headers=HEADERS).reason == "stale_conversation"
        assert revocations == ["paired_room_revoked"]
        assert closed == [handle]
        assert _row(store, handle) == ("paired_room", "closed", "paired_room_revoked")
        assert (
            sum(frame.get("method") == "session.interrupt" for frame in socket.sent)
            == 1
        )
    finally:
        bridge.close()
        store.close()
