from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from observations.configuration import (
    InvalidConfiguration,
    fields,
    name,
)
from observations.models import Feed, Source
from observations.subscriptions import Consumer
from observations.yaml import load_yaml

from .registered import CONNECTORS
from .registry import ConfiguredConnectors, ConnectorRegistry


def validate(
    value: object,
    approved: dict[str, Any] | None = None,
    *,
    registry: ConnectorRegistry = CONNECTORS,
    resolve_template: bool = False,
) -> dict[str, Any]:
    catalog = fields(value, {"version", "sources"})

    if type(catalog["version"]) is not int or catalog["version"] != 2:
        raise InvalidConfiguration("Source catalog requires version 2")
    if type(catalog["sources"]) is not dict or len(catalog["sources"]) > 16:
        raise InvalidConfiguration("Define at most sixteen named sources")
    if resolve_template and approved is None:
        raise InvalidConfiguration("Template binding requires host policy")

    sources = {}

    for identifier, raw in catalog["sources"].items():
        name(identifier)

        if resolve_template and approved is not None:
            if type(raw) is not dict:
                raise InvalidConfiguration("Source requires a connector")

            raw = registry.get(raw.get("connector")).prepare_template(
                raw, approved
            )

        sources[identifier] = registry.validate(raw)

    result = {"version": 2, "sources": sources}

    if approved is not None:
        resolve_sources(result, registry.configure(approved))

    return result


def load(
    path: str | Path,
    approved: dict[str, Any] | None = None,
    *,
    resolve_template: bool = False,
    registry: ConnectorRegistry = CONNECTORS,
) -> dict[str, Any]:
    return validate(
        load_yaml(Path(path)),
        approved,
        registry=registry,
        resolve_template=resolve_template,
    )


def resolve_sources(
    catalog: dict[str, Any], connectors: ConfiguredConnectors
) -> dict[str, Source]:
    return {
        alias: connectors.resolve(definition)
        for alias, definition in catalog["sources"].items()
    }


def plan(
    sources: Mapping[str, Source], consumers: tuple[Consumer, ...]
) -> dict[str, Any]:
    feeds: dict[str, dict[str, Any]] = {}
    subscriptions: dict[str, dict[str, str]] = {}

    for consumer in consumers:
        if consumer.identifier in subscriptions:
            raise InvalidConfiguration("Consumer IDs must be unique")

        subscriptions[consumer.identifier] = {}

        for subscription in consumer.subscriptions:
            if subscription.source not in sources:
                raise InvalidConfiguration(
                    "Subscription source is not defined"
                )
            if not consumer.enabled:
                continue

            source = sources[subscription.source]
            key = source.key
            subscriptions[consumer.identifier][subscription.source] = key
            existing = feeds.get(key)

            if existing is None:
                feeds[key] = {
                    "source": asdict(source),
                    "aliases": [subscription.source],
                    "interval": subscription.interval,
                    "lookback": subscription.lookback,
                    "overlap": subscription.overlap,
                }
            else:
                existing["aliases"] = sorted(
                    set(existing["aliases"]) | {subscription.source}
                )
                existing["interval"] = min(
                    existing["interval"], subscription.interval
                )
                existing["lookback"] = max(
                    existing["lookback"], subscription.lookback
                )
                existing["overlap"] = max(
                    existing["overlap"], subscription.overlap
                )

    if len(consumers) > 16:
        raise InvalidConfiguration("Define at most sixteen consumers")

    return {
        "version": 1,
        "feeds": feeds,
        "consumers": subscriptions,
        "consumer_revisions": {
            consumer.identifier: consumer.revision for consumer in consumers
        },
    }


def feeds(compiled: dict[str, Any]) -> tuple[Feed, ...]:
    return tuple(
        Feed(
            Source(**item["source"]),
            tuple(item["aliases"]),
            item["interval"],
            item["lookback"],
            item["overlap"],
        )
        for item in compiled["feeds"].values()
    )
