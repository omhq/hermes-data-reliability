from dataclasses import dataclass, replace
from typing import Any

from .configuration import (
    InvalidConfiguration,
    duration,
    fields,
    fingerprint,
    name,
)


@dataclass(frozen=True)
class Subscription:
    source: str
    interval: int
    lookback: int
    overlap: int


@dataclass(frozen=True)
class Consumer:
    identifier: str
    enabled: bool
    subscriptions: tuple[Subscription, ...]
    revision: str


def parse(value: object) -> Consumer:
    manifest = fields(value, {"id", "enabled", "subscriptions"})
    identifier = name(manifest["id"])

    if type(manifest["enabled"]) is not bool:
        raise ValueError("Consumer enabled must be a boolean")

    raw = manifest["subscriptions"]

    if type(raw) is not dict or len(raw) > 16:
        raise ValueError("Define at most sixteen subscriptions per consumer")

    subscriptions = []

    for source, raw_settings in raw.items():
        settings = fields(
            raw_settings, {"poll_interval", "initial_lookback", "overlap"}
        )
        subscriptions.append(
            Subscription(
                name(source),
                duration(settings["poll_interval"], 15, 3600),
                duration(settings["initial_lookback"], 1, 86400),
                duration(settings["overlap"], 0, 3600),
            )
        )

    return Consumer(
        identifier,
        manifest["enabled"],
        tuple(subscriptions),
        fingerprint({"consumer": manifest}),
    )


def manifest(definition: dict[str, Any]) -> Consumer:
    return replace(
        parse(definition.get("consumer")), revision=fingerprint(definition)
    )


def bindings(consumer: Consumer, plan: dict[str, Any]) -> dict[str, str]:
    if (
        plan["consumer_revisions"].get(consumer.identifier)
        != consumer.revision
    ):
        raise InvalidConfiguration(
            "Consumer configuration differs from the compiled collection plan"
        )

    return plan["consumers"][consumer.identifier]
