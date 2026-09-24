"""The framework-independent HTTP response value shared by Home surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    status: int
    body: dict[str, object] | str = field(repr=False)
    content_type: str = "application/json; charset=utf-8"
    headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)
