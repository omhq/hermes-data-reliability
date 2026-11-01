import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from .validation import (
    InvalidDefinition,
    duration,
    mapping,
    timestamp,
    utc_text,
)


@dataclass(frozen=True)
class Occurrence:
    scheduled_at: int

    @property
    def identifier(self) -> str:
        return utc_text(self.scheduled_at)


class Schedule(Protocol):
    @property
    def period_seconds(self) -> int: ...

    def occurrences(self, start: int, end: int) -> Iterator[Occurrence]: ...

    def contains(self, occurrence: Occurrence) -> bool: ...

    def describe(self) -> dict[str, object]: ...


@dataclass(frozen=True)
class IntervalSchedule:
    period_seconds: int
    anchor: int

    def occurrences(self, start: int, end: int) -> Iterator[Occurrence]:
        first = (
            self.anchor
            + max(
                0,
                (start - self.anchor + self.period_seconds - 1)
                // self.period_seconds,
            )
            * self.period_seconds
        )

        for scheduled in range(first, end + 1, self.period_seconds):
            yield Occurrence(scheduled)

    def contains(self, occurrence: Occurrence) -> bool:
        return (
            occurrence.scheduled_at >= self.anchor
            and (occurrence.scheduled_at - self.anchor) % self.period_seconds
            == 0
        )

    def describe(self) -> dict[str, object]:
        return {
            "kind": "interval",
            "every_seconds": self.period_seconds,
            "anchor": utc_text(self.anchor),
        }


@dataclass(frozen=True)
class DailySchedule:
    seconds_after_midnight: int
    period_seconds: int = 86400

    def occurrences(self, start: int, end: int) -> Iterator[Occurrence]:
        first = start // 86400 * 86400 + self.seconds_after_midnight

        if first < start:
            first += 86400

        for scheduled in range(first, end + 1, 86400):
            yield Occurrence(scheduled)

    def contains(self, occurrence: Occurrence) -> bool:
        return occurrence.scheduled_at % 86400 == self.seconds_after_midnight

    def describe(self) -> dict[str, object]:
        return {
            "kind": "daily",
            "timezone": "UTC",
            "at": f"{self.seconds_after_midnight // 3600:02}:"
            f"{self.seconds_after_midnight % 3600 // 60:02}",
        }


def parse(value: object) -> Schedule:
    tagged = mapping(value, {"kind"}, {"every", "anchor", "at", "timezone"})

    if tagged["kind"] == "interval":
        interval = mapping(value, {"kind", "every", "anchor"})
        return IntervalSchedule(
            duration(interval["every"], 60, 86400),
            timestamp(interval["anchor"]),
        )
    if tagged["kind"] == "daily":
        daily = mapping(value, {"kind", "at", "timezone"})
        at = daily["at"]

        if (
            daily["timezone"] != "UTC"
            or type(at) is not str
            or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", at)
        ):
            raise InvalidDefinition("Daily schedules require HH:MM and UTC")

        return DailySchedule(int(at[:2]) * 3600 + int(at[3:]) * 60)

    raise InvalidDefinition("Unknown registered schedule")


def resolve(schedule: Schedule, value: object) -> Occurrence:
    occurrence = Occurrence(timestamp(value))

    if not schedule.contains(occurrence):
        raise InvalidDefinition("Occurrence does not belong to this schedule")

    return occurrence
