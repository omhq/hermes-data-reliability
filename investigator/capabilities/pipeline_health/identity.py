import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from .schedules import Occurrence
from .validation import InvalidDefinition, mapping, name


class Identity(Protocol):
    def resolve(self, occurrence: Occurrence) -> str: ...

    def describe(self) -> dict[str, object]: ...


@dataclass(frozen=True)
class DateIdentity:
    def resolve(self, occurrence: Occurrence) -> str:
        return (
            datetime.fromtimestamp(occurrence.scheduled_at, UTC)
            .date()
            .isoformat()
        )

    def describe(self) -> dict[str, object]:
        return {"kind": "date"}


@dataclass(frozen=True)
class TimestampIdentity:
    timestamp_format: str

    def resolve(self, occurrence: Occurrence) -> str:
        instant = datetime.fromtimestamp(occurrence.scheduled_at, UTC)

        if self.timestamp_format == "utc_seconds":
            return instant.strftime("%Y-%m-%dT%H:%M:%SZ")

        return instant.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def describe(self) -> dict[str, object]:
        return {"kind": "timestamp", "timestamp_format": self.timestamp_format}


@dataclass(frozen=True)
class HashIdentity:
    timestamp: TimestampIdentity
    length: int

    def resolve(self, occurrence: Occurrence) -> str:
        return hashlib.sha256(
            self.timestamp.resolve(occurrence).encode()
        ).hexdigest()[: self.length]

    def describe(self) -> dict[str, object]:
        return {
            "kind": "sha256",
            "length": self.length,
            "timestamp_format": self.timestamp.timestamp_format,
        }


def parse(value: object) -> Identity:
    tagged = mapping(value, {"kind"}, {"length", "timestamp_format"})

    kind = name(tagged["kind"])

    if kind == "date":
        mapping(value, {"kind"})
        return DateIdentity()
    if kind not in {"sha256", "timestamp"}:
        raise InvalidDefinition("Unknown registered identity mapping")

    required = {"kind", "timestamp_format"}

    if kind == "sha256":
        required.add("length")

    identity = mapping(value, required)
    format_name = identity["timestamp_format"]

    if type(format_name) is not str or format_name not in {
        "utc_seconds",
        "utc_milliseconds",
    }:
        raise InvalidDefinition("Unknown timestamp serialization")

    formatted = TimestampIdentity(format_name)

    if kind == "timestamp":
        return formatted

    length = identity["length"]

    if type(length) is not int or not 8 <= length <= 64:
        raise InvalidDefinition(
            "SHA-256 prefix length must be between 8 and 64"
        )

    return HashIdentity(formatted, length)
