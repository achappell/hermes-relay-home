"""Bounded, content-safe state for a Home Watch View."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

WATCH_VIEW_CAPABILITY = "watch_view"
WATCH_ACTIVITY_STATES = frozenset(
    {
        "ready",
        "open",
        "capture",
        "turn",
        "playback",
        "response_ready",
        "playback_complete",
        "idle",
    }
)
WATCH_ROUTE_CLASSES = frozenset({"home"})
WATCH_ROUTE_FIELDS = frozenset({"class", "id"})
WATCH_HEALTH_STATES = frozenset({"ready"})


@dataclass(frozen=True, slots=True)
class WatchSnapshot:
    """Internal safe state projected to an authorized Watch caller."""

    device_id: str
    profile_id: str
    configuration_revision: int
    credential_generation: int | None
    activity: str
    route: Mapping[str, str]
    health: str = "ready"
    session_present: bool = False

    def __post_init__(self) -> None:
        _bounded_text(self.device_id, "device_id")
        _bounded_text(self.profile_id, "profile_id")
        if (
            type(self.configuration_revision) is not int
            or self.configuration_revision < 0
        ):
            raise ValueError("configuration revision must be a non-negative integer")
        if self.credential_generation is not None and (
            type(self.credential_generation) is not int
            or self.credential_generation < 1
        ):
            raise ValueError("credential generation must be a positive integer")
        if self.activity not in WATCH_ACTIVITY_STATES:
            raise ValueError("watch activity state is invalid")
        serialize_watch_route(self.route)
        if self.health not in WATCH_HEALTH_STATES:
            raise ValueError("watch health state is invalid")
        if type(self.session_present) is not bool:
            raise ValueError("session_present must be a boolean")


class WatchSnapshotProvider(Protocol):
    """Read-only source for one current endpoint observation."""

    def watch_snapshot(
        self,
        device_id: str,
        *,
        configuration_revision: int,
    ) -> WatchSnapshot | None: ...


def serialize_watch_route(route: Mapping[str, str]) -> dict[str, str]:
    """Serialize only the route fields permitted in a Watch response."""
    if not isinstance(route, Mapping):
        raise TypeError("watch route must be an object")
    if set(route) != WATCH_ROUTE_FIELDS:
        raise ValueError("watch route contains unsupported fields")
    route_class = route.get("class")
    if route_class not in WATCH_ROUTE_CLASSES:
        raise ValueError("watch route class is invalid")
    route_id = _bounded_text(route.get("id"), "route.id")
    return {"class": route_class, "id": route_id}


def activity_summary(activity: str, *, session_present: bool) -> str:
    """Return a bounded content-free task summary for a Watch response."""
    if activity == "capture":
        return "Listening"
    if activity == "turn":
        return "Processing the current turn"
    if activity == "playback":
        return "Speaking"
    if activity == "response_ready":
        return "Response ready"
    if activity == "idle" and session_present:
        return "Conversation idle"
    if activity == "playback_complete" and session_present:
        return "Conversation ready"
    return "Conversation ready" if session_present else "Conversation starting"


def _bounded_text(value: object, field: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= 128:
        raise ValueError(f"{field} must be a string of 1-128 characters")
    if "\r" in value or "\n" in value:
        raise ValueError(f"{field} must not contain line breaks")
    return value


def monotonic_clock() -> float:
    return time.monotonic()


__all__ = [
    "WATCH_ACTIVITY_STATES",
    "WATCH_HEALTH_STATES",
    "WATCH_ROUTE_CLASSES",
    "WATCH_ROUTE_FIELDS",
    "WATCH_VIEW_CAPABILITY",
    "WatchSnapshot",
    "WatchSnapshotProvider",
    "activity_summary",
    "monotonic_clock",
    "serialize_watch_route",
]
