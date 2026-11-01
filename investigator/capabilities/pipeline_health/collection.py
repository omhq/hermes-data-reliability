from typing import Any

from observations.subscriptions import bindings as consumer_bindings

from .definition import Definition
from .validation import InvalidDefinition


def with_collection(
    config: dict[str, Any], definition: Definition, plan: dict[str, Any]
) -> dict[str, Any]:
    bindings = consumer_bindings(definition.consumer, plan)
    requested = (
        {
            item.source: item.overlap
            for item in definition.consumer.subscriptions
        }
        if definition.consumer.enabled
        else {}
    )

    if set(bindings) != set(requested):
        raise InvalidDefinition(
            "Consumer subscriptions changed; recompile the graph"
        )

    profiles = dict(config.get("health_resources", {}))

    for alias, key in bindings.items():
        if alias in profiles:
            raise InvalidDefinition("Source and health resource names overlap")

        feed = plan["feeds"][key]
        source = feed["source"]
        profiles[alias] = {
            "adapter": "collected_events",
            "source_key": key,
            "connector": source["connector"],
            "account": source["account"],
            "region": source["region"],
            "location": source["location"],
            "overlap": requested[alias],
        }

    for source in definition.sources.values():
        if not definition.consumer.enabled:
            continue

        if source.adapter != "collected_events":
            continue
        if source.resource not in bindings:
            raise InvalidDefinition(
                "Collected evidence needs an active subscription"
            )

    return {**config, "health_resources": profiles}
