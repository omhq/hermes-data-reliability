from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Source:
    key: str
    connector: str
    account: str
    region: str
    location: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Feed:
    source: Source
    aliases: tuple[str, ...]
    interval: int
    lookback: int
    overlap: int


@dataclass(frozen=True)
class Event:
    identifier: str
    timestamp: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class Page:
    events: tuple[Event, ...]
    continuation: str | None


class Reader(Protocol):
    def read(
        self, source: Source, start: int, end: int, token: str | None
    ) -> Page: ...
