"""Home-owned freshness and replay checks for typed choice prompts."""

from __future__ import annotations

import math
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from hermes_home.bridge.standard import (
    MAX_TYPED_CHOICE_REVISIONS_PER_TURN,
    BridgeEvent,
)

CHOICE_EVENT_TYPE = "prompt.request"
CHOICE_EXPIRY_EVENT_TYPE = "prompt.expire"
CHOICE_TTL_SECONDS = 300.0
MAX_CHOICE_OPTIONS = 32
MAX_CHOICE_ID_LENGTH = 64
MAX_CHOICE_TEXT_LENGTH = 1024
MAX_CHOICE_LABEL_LENGTH = 256
MAX_CHOICE_CORRELATIONS = 256
MAX_CHOICE_OBJECTS_PER_TURN = MAX_TYPED_CHOICE_REVISIONS_PER_TURN

_OPERATIONS = ("choose", "explore")
_RESPONSE_FIELDS = frozenset({"operation", "option_id", "object_id", "freshness"})
_SourceKey = tuple[str, str, str, int, str, str]
_PromptKey = tuple[str, str, str, str]
_TurnKey = tuple[str, str]


class ChoiceProtocolError(ValueError):
    """A typed choice event cannot be projected safely."""


@dataclass(slots=True)
class _ChoiceObject:
    source_key: _SourceKey
    handle: str
    turn_id: str
    source_object_id: str
    source_freshness: str
    public_object_id: str
    public_freshness: str
    source_operations: tuple[str, ...]
    supported_operations: tuple[str, ...]
    options: tuple[tuple[str, str], ...]
    expires_at: float | None = None
    state: str = "active"


@dataclass(slots=True)
class _ChoiceCorrelation:
    event: BridgeEvent
    choice: _ChoiceObject
    consumed: bool = False


@dataclass(frozen=True, slots=True)
class ChoiceDecision:
    """One validated action or a content-free unavailable reason."""

    reason: str | None
    operation: str | None = None
    event: BridgeEvent | None = field(default=None, repr=False)
    response: dict[str, object] | None = field(default=None, repr=False)
    _choice: _ChoiceObject | None = field(default=None, repr=False)


class ChoiceAuthority:
    """Mint endpoint-scoped choice tokens and accept each response once."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._clock = clock
        self._token_factory = token_factory or (lambda: uuid.uuid4().hex)
        self._objects: dict[_SourceKey, _ChoiceObject] = {}
        self._current: dict[_TurnKey, _ChoiceObject] = {}
        self._correlations: OrderedDict[_PromptKey, _ChoiceCorrelation] = OrderedDict()

    def project(self, event: BridgeEvent, *, handle: str) -> dict[str, object]:
        """Validate and expose only the small public choice projection."""

        if (
            event.type != CHOICE_EVENT_TYPE
            or event.conversation_handle != handle
            or event.turn_id is None
            or event.correlation_id is None
        ):
            raise ChoiceProtocolError("choice event has incomplete Home binding")
        payload = event.payload
        text, source_object_id, source_freshness, source_operations, options = (
            _normalize_choice_payload(payload)
        )
        if (
            type(event.standard_session_id) is not str
            or not event.standard_session_id
            or type(event.configuration_revision) is not int
            or event.configuration_revision < 0
        ):
            raise ChoiceProtocolError("choice event has incomplete claim binding")

        supported_operations = tuple(
            operation
            for operation in source_operations
            if f"prompt.{operation}" in event.choice_capabilities
        )
        source_key = (
            handle,
            event.turn_id,
            event.standard_session_id,
            event.configuration_revision,
            source_object_id,
            source_freshness,
        )
        turn_key = (handle, event.turn_id)
        prompt_key = (handle, event.turn_id, event.correlation_id, event.type)
        now = self._now()

        if prompt_key in self._correlations:
            raise ChoiceProtocolError("choice correlation was already issued")

        choice = self._objects.get(source_key)
        if choice is not None:
            if (
                choice.options != options
                or choice.source_operations != source_operations
            ):
                if choice.state == "active":
                    choice.state = "replaced"
                raise ChoiceProtocolError("choice changed without a new freshness ID")
            if (
                choice.state == "active"
                and choice.supported_operations != supported_operations
            ):
                choice.state = "revoked"
                if self._current.get(turn_key) is choice:
                    self._current.pop(turn_key, None)
            if (
                choice.state == "active"
                and choice.expires_at is not None
                and now >= choice.expires_at
            ):
                choice.state = "expired"
                if self._current.get(turn_key) is choice:
                    self._current.pop(turn_key, None)
        else:
            current = self._current.get(turn_key)
            active_objects = sum(
                1
                for current_key, candidate in self._objects.items()
                if current_key[:2] == turn_key
                and candidate.state
                in {"active", "expired", "replaced", "revoked", "chosen"}
            )
            if active_objects >= MAX_CHOICE_OBJECTS_PER_TURN:
                raise ChoiceProtocolError("choice revision limit exceeded")
            if current is not None and current.state == "active":
                current.state = "replaced"
            public_object_id = self._new_token()
            public_freshness = self._new_token()
            if public_object_id == public_freshness:
                raise ChoiceProtocolError("choice tokens must be distinct")
            choice = _ChoiceObject(
                source_key=source_key,
                handle=handle,
                turn_id=event.turn_id,
                source_object_id=source_object_id,
                source_freshness=source_freshness,
                public_object_id=public_object_id,
                public_freshness=public_freshness,
                source_operations=source_operations,
                supported_operations=supported_operations,
                options=options,
            )
            self._objects[source_key] = choice
            self._current[turn_key] = choice

        if len(self._correlations) >= MAX_CHOICE_CORRELATIONS:
            self._prune_inactive_correlations()
        if len(self._correlations) >= MAX_CHOICE_CORRELATIONS:
            raise ChoiceProtocolError("choice correlation limit exceeded")
        self._correlations[prompt_key] = _ChoiceCorrelation(event=event, choice=choice)

        is_current = self._current.get(turn_key) is choice and choice.state == "active"
        operations = list(choice.supported_operations) if is_current else []
        remaining = self._remaining_seconds(choice, now)
        return {
            "prompt_id": event.correlation_id,
            "prompt_kind": "choice",
            "text": text,
            "timeout_s": remaining,
            "options": [
                {"id": option_id, "label": label} for option_id, label in options
            ],
            "choice": {
                "object_id": choice.public_object_id,
                "freshness": choice.public_freshness,
                "operations": operations,
            },
        }

    def mark_delivered(
        self, *, handle: str, turn_id: str, correlation_id: str
    ) -> int | None:
        """Start or read the freshness window immediately before first send."""

        prompt_key = (handle, turn_id, correlation_id, CHOICE_EVENT_TYPE)
        correlation = self._correlations.get(prompt_key)
        if correlation is None:
            return None
        choice = correlation.choice
        if (
            choice.state != "active"
            or self._current.get((handle, turn_id)) is not choice
        ):
            return 0
        now = self._now()
        if choice.expires_at is None:
            choice.expires_at = now + CHOICE_TTL_SECONDS
        elif now >= choice.expires_at:
            choice.state = "expired"
            self._current.pop((handle, turn_id), None)
            return 0
        return self._remaining_seconds(choice, now)

    def authorize(
        self,
        *,
        handle: str,
        turn_id: str,
        correlation_id: str,
        event_type: str,
        response: Mapping[str, object],
        active_turn_id: str | None,
        ready: bool,
        event: BridgeEvent | None,
    ) -> ChoiceDecision:
        """Validate authority and consume the correlation before delivery."""

        prompt_key = (handle, turn_id, correlation_id, CHOICE_EVENT_TYPE)
        correlation = self._correlations.get(prompt_key)
        if correlation is None:
            return ChoiceDecision(
                self._unknown_context_reason(handle, turn_id, correlation_id)
            )
        if event_type != CHOICE_EVENT_TYPE:
            return ChoiceDecision("stale")
        if correlation.consumed:
            return ChoiceDecision("duplicate")
        choice = correlation.choice
        if not ready or choice.handle != handle:
            return ChoiceDecision("revoked")
        if choice.state == "revoked":
            return ChoiceDecision("revoked")
        if choice.state == "expired":
            return ChoiceDecision("expired")
        if choice.expires_at is not None and self._now() >= choice.expires_at:
            choice.state = "expired"
            return ChoiceDecision("expired")
        if (
            choice.state == "replaced"
            or self._current.get((handle, turn_id)) is not choice
        ):
            return ChoiceDecision("replaced")
        if choice.expires_at is None:
            return ChoiceDecision("unknown")
        if active_turn_id != turn_id or event is not correlation.event:
            return ChoiceDecision("stale")
        if choice.state == "chosen":
            return ChoiceDecision("duplicate")

        if set(response) != _RESPONSE_FIELDS:
            return ChoiceDecision("unsupported")
        operation = response.get("operation")
        option_id = response.get("option_id")
        object_id = response.get("object_id")
        freshness = response.get("freshness")
        if type(operation) is not str or operation not in _OPERATIONS:
            return ChoiceDecision("unsupported")
        if operation not in choice.supported_operations:
            return ChoiceDecision("unsupported")
        if (
            not _valid_identifier(option_id)
            or not _valid_identifier(object_id)
            or not _valid_identifier(freshness)
        ):
            return ChoiceDecision("unknown")
        if object_id != choice.public_object_id or freshness != choice.public_freshness:
            return ChoiceDecision("stale")
        if option_id not in {item[0] for item in choice.options}:
            return ChoiceDecision("unknown")

        correlation.consumed = True
        return ChoiceDecision(
            reason=None,
            operation=operation,
            event=correlation.event,
            response={
                "operation": operation,
                "option_id": option_id,
                "object_id": choice.source_object_id,
                "freshness": choice.source_freshness,
            },
            _choice=choice,
        )

    def complete(self, decision: ChoiceDecision) -> None:
        """Close a chosen object after Home confirms its response."""

        if (
            decision.reason is None
            and decision.operation == "choose"
            and decision._choice is not None
            and self._current.get((decision._choice.handle, decision._choice.turn_id))
            is decision._choice
            and decision._choice.state == "active"
        ):
            decision._choice.state = "chosen"

    def expire_correlation(
        self, *, handle: str, turn_id: str, correlation_id: str
    ) -> None:
        """Expire a choice when the upstream sends its correlated expiry event."""

        key = (handle, turn_id, correlation_id, CHOICE_EVENT_TYPE)
        correlation = self._correlations.get(key)
        if correlation is not None and correlation.choice.state == "active":
            correlation.choice.state = "expired"
            if self._current.get((handle, turn_id)) is correlation.choice:
                self._current.pop((handle, turn_id), None)

    def revoke_turn(self, *, handle: str, turn_id: str) -> None:
        """Revoke every choice bound to a completed or retired turn."""

        turn_key = (handle, turn_id)
        current = self._current.pop(turn_key, None)
        if current is not None and current.state == "active":
            current.state = "revoked"
        for source_key, choice in tuple(self._objects.items()):
            if source_key[:2] == turn_key:
                if choice.state == "active":
                    choice.state = "revoked"
                self._objects.pop(source_key, None)

    def revoke_all(self) -> None:
        """Revoke current objects after authorization or transport loss."""

        for choice in self._objects.values():
            if choice.state == "active":
                choice.state = "revoked"
        self._current.clear()
        self._objects.clear()

    def knows_correlation(self, correlation_id: str) -> bool:
        return any(key[2] == correlation_id for key in self._correlations)

    def _unknown_context_reason(
        self, handle: str, turn_id: str, correlation_id: str
    ) -> str:
        for key in self._correlations:
            if key[2] != correlation_id:
                continue
            if key[0] != handle:
                return "revoked"
            if key[1] != turn_id:
                return "stale"
        return "unknown"

    def _now(self) -> float:
        try:
            value = self._clock()
        except (OverflowError, TypeError, ValueError) as error:
            raise ChoiceProtocolError("choice clock is invalid") from error
        if type(value) not in {int, float} or not math.isfinite(value):
            raise ChoiceProtocolError("choice clock is invalid")
        return float(value)

    def _new_token(self) -> str:
        try:
            token = self._token_factory()
        except Exception as error:  # token source is injected
            raise ChoiceProtocolError("choice token source failed") from error
        if not _valid_identifier(token):
            raise ChoiceProtocolError("choice token is invalid")
        if any(
            token in {choice.public_object_id, choice.public_freshness}
            for choice in self._objects.values()
        ):
            raise ChoiceProtocolError("choice token collision")
        return token

    def _prune_inactive_correlations(self) -> None:
        for key, correlation in tuple(self._correlations.items()):
            choice = correlation.choice
            if (
                choice.state != "active"
                or self._current.get((choice.handle, choice.turn_id)) is not choice
            ):
                del self._correlations[key]

    @staticmethod
    def _remaining_seconds(choice: _ChoiceObject, now: float) -> int:
        if choice.state != "active":
            return 0
        if choice.expires_at is None:
            return int(CHOICE_TTL_SECONDS)
        return max(0, math.ceil(choice.expires_at - now))


def _normalize_choice_payload(
    payload: Mapping[str, object],
) -> tuple[str, str, str, tuple[str, ...], tuple[tuple[str, str], ...]]:
    if payload.get("prompt_kind") != "choice":
        raise ChoiceProtocolError("typed choice prompt kind is invalid")
    for key in ("sensitive",):
        if key in payload and type(payload[key]) is not bool:
            raise ChoiceProtocolError("typed choice privacy flag is invalid")
        if payload.get(key) is True:
            raise ChoiceProtocolError("sensitive prompts cannot become typed choices")
    text = payload.get("text")
    if type(text) is not str or not text.strip() or len(text) > MAX_CHOICE_TEXT_LENGTH:
        raise ChoiceProtocolError("typed choice explanation is invalid")
    choice = payload.get("choice")
    if not isinstance(choice, Mapping):
        raise ChoiceProtocolError("typed choice identity is missing")
    if "sensitive" in choice and type(choice["sensitive"]) is not bool:
        raise ChoiceProtocolError("typed choice privacy flag is invalid")
    if choice.get("sensitive") is True:
        raise ChoiceProtocolError("sensitive prompts cannot become typed choices")
    source_object_id = choice.get("object_id")
    source_freshness = choice.get("freshness")
    if not _valid_identifier(source_object_id) or not _valid_identifier(
        source_freshness
    ):
        raise ChoiceProtocolError("typed choice freshness identity is invalid")
    raw_operations = choice.get("operations")
    if (
        not isinstance(raw_operations, list)
        or not raw_operations
        or any(
            type(operation) is not str or operation not in _OPERATIONS
            for operation in raw_operations
        )
        or len(set(raw_operations)) != len(raw_operations)
    ):
        raise ChoiceProtocolError("typed choice operations are invalid")
    raw_options = payload.get("options")
    if (
        not isinstance(raw_options, list)
        or not raw_options
        or len(raw_options) > MAX_CHOICE_OPTIONS
    ):
        raise ChoiceProtocolError("typed choice option count is invalid")
    options: list[tuple[str, str]] = []
    option_ids: set[str] = set()
    for raw_option in raw_options:
        if not isinstance(raw_option, Mapping):
            raise ChoiceProtocolError("typed choice option is invalid")
        if "sensitive" in raw_option and type(raw_option["sensitive"]) is not bool:
            raise ChoiceProtocolError("typed choice option privacy flag is invalid")
        if raw_option.get("sensitive") is True:
            raise ChoiceProtocolError("sensitive options cannot become typed choices")
        option_id = raw_option.get("id")
        label = raw_option.get("label")
        if not _valid_identifier(option_id):
            raise ChoiceProtocolError("typed choice option ID is invalid")
        if (
            type(label) is not str
            or not label.strip()
            or len(label) > MAX_CHOICE_LABEL_LENGTH
        ):
            raise ChoiceProtocolError("typed choice option label is invalid")
        if option_id in option_ids:
            raise ChoiceProtocolError("typed choice option IDs are duplicated")
        option_ids.add(option_id)
        options.append((option_id, label))
    return (
        text,
        source_object_id,
        source_freshness,
        tuple(raw_operations),
        tuple(options),
    )


def _valid_identifier(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and len(value) <= MAX_CHOICE_ID_LENGTH
        and not any(ord(character) < 32 for character in value)
    )
