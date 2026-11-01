import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from incidents.specification import DefinitionError, validate_investigation
from observations.subscriptions import Consumer, manifest

from . import identity, schedules
from .adapters import ADAPTERS
from .checks import CHECKS
from .observations import Target
from .validation import InvalidDefinition, duration, mapping, name


@dataclass(frozen=True)
class Source:
    name: str
    adapter: str
    resource: str
    options: Mapping[str, object]

    def target(
        self,
        resources: Mapping[str, Mapping[str, object]],
        delivery_id: str,
        account: str,
    ) -> Target:
        if (
            self.resource not in resources
            or resources[self.resource]["adapter"] != self.adapter
        ):
            raise InvalidDefinition("Evidence resource is not approved")

        return ADAPTERS[self.adapter].resolve(
            self.resource,
            resources[self.resource],
            self.options,
            delivery_id,
            account,
        )

    def describe(self) -> dict[str, object]:
        return {
            "adapter": self.adapter,
            "resource": self.resource,
            **self.options,
        }


@dataclass(frozen=True)
class Expectation:
    name: str
    check: str
    source: Source

    def describe(self) -> dict[str, object]:
        return {
            "id": self.name,
            "check": self.check,
            "source": self.source.name,
        }


@dataclass(frozen=True)
class Pipeline:
    name: str
    schedule: schedules.Schedule
    identity: identity.Identity
    grace_seconds: int
    expectations: tuple[Expectation, ...]
    investigation_sources: tuple[Source, ...]
    investigation: Mapping[str, str]
    revision: str

    def occurrence(self, value: object) -> schedules.Occurrence:
        return schedules.resolve(self.schedule, value)

    def deadline(self, occurrence: schedules.Occurrence) -> int:
        return occurrence.scheduled_at + self.grace_seconds

    def expectation(self, identifier: object) -> Expectation:
        for expectation in self.expectations:
            if expectation.name == identifier:
                return expectation

        raise InvalidDefinition("Unknown pipeline expectation")

    def describe(
        self, *, include_instructions: bool = True
    ) -> dict[str, object]:
        return {
            "pipeline_id": self.name,
            "revision": self.revision,
            "schedule": self.schedule.describe(),
            "identity": self.identity.describe(),
            "grace_seconds": self.grace_seconds,
            "expectations": [item.describe() for item in self.expectations],
            "investigation_sources": [
                item.name for item in self.investigation_sources
            ],
            "investigation": (
                dict(self.investigation)
                if include_instructions
                else {"objective": self.investigation["objective"]}
            ),
        }


@dataclass(frozen=True)
class Definition:
    consumer: Consumer
    sources: Mapping[str, Source]
    pipelines: tuple[Pipeline, ...]


def parse(value: object) -> Definition:
    definition = mapping(
        value, {"version", "consumer", "sources", "pipelines"}
    )

    if type(definition["version"]) is not int or definition["version"] != 4:
        raise InvalidDefinition(
            "Pipeline health requires definition version 4"
        )

    consumer = manifest(definition)

    raw_sources = definition["sources"]
    raw_pipelines = definition["pipelines"]

    if type(raw_sources) is not dict or len(raw_sources) > 16:
        raise InvalidDefinition("Define at most sixteen evidence sources")
    if type(raw_pipelines) is not dict or len(raw_pipelines) > 8:
        raise InvalidDefinition("Define at most eight pipelines")

    sources = {}

    for identifier, raw in raw_sources.items():
        identifier = name(identifier)
        if type(raw) is not dict:
            raise InvalidDefinition("Invalid evidence source")

        adapter = name(raw.get("adapter"))
        resource = name(raw.get("resource"))

        if adapter not in ADAPTERS:
            raise InvalidDefinition("Unknown registered evidence adapter")

        options = ADAPTERS[adapter].source_options(
            {
                key: value
                for key, value in raw.items()
                if key not in {"adapter", "resource"}
            }
        )
        sources[identifier] = Source(identifier, adapter, resource, options)

    pipelines = []
    checks_per_day = 0

    for identifier, raw in raw_pipelines.items():
        identifier = name(identifier)
        pipeline = mapping(
            raw,
            {
                "schedule",
                "identity",
                "grace_period",
                "expectations",
                "investigation",
            },
            {"investigation_sources"},
        )
        try:
            investigation = validate_investigation(
                pipeline["investigation"], defaults=True
            )
        except DefinitionError as error:
            raise InvalidDefinition(str(error)) from None

        schedule = schedules.parse(pipeline["schedule"])
        mapping_identity = identity.parse(pipeline["identity"])

        if (
            isinstance(mapping_identity, identity.DateIdentity)
            and schedule.period_seconds < 86400
        ):
            raise InvalidDefinition(
                "Date identity cannot distinguish subdaily deliveries"
            )

        raw_expectations = pipeline["expectations"]

        if (
            type(raw_expectations) is not list
            or not 1 <= len(raw_expectations) <= 8
        ):
            raise InvalidDefinition(
                "Define one to eight expectations per pipeline"
            )

        expectations = []
        used_names: set[str] = set()
        referenced: set[str] = set()

        for raw_expectation in raw_expectations:
            expectation = mapping(raw_expectation, {"id", "check", "source"})
            check_id = name(expectation["id"])
            check = name(expectation["check"])
            source_id = name(expectation["source"])

            if (
                check_id in used_names
                or check not in CHECKS
                or source_id not in sources
            ):
                raise InvalidDefinition(
                    "Unknown or duplicate expectation definition"
                )

            if sources[source_id].adapter != CHECKS[check].adapter:
                raise InvalidDefinition("Check and evidence adapter disagree")

            used_names.add(check_id)
            referenced.add(source_id)
            expectations.append(
                Expectation(check_id, check, sources[source_id])
            )

        additional = pipeline.get("investigation_sources", [])

        if type(additional) is not list or len(additional) > 4:
            raise InvalidDefinition(
                "Define at most four investigation sources"
            )

        supporting = []

        for source_id in additional:
            source_id = name(source_id)

            if source_id not in sources or source_id in {
                item.name for item in supporting
            }:
                raise InvalidDefinition(
                    "Unknown or duplicate investigation source"
                )

            supporting.append(sources[source_id])
            referenced.add(source_id)

        revision = hashlib.sha256(
            json.dumps(
                {
                    "pipeline": raw,
                    "sources": {
                        source_id: raw_sources[source_id]
                        for source_id in sorted(referenced)
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        pipelines.append(
            Pipeline(
                identifier,
                schedule,
                mapping_identity,
                duration(pipeline["grace_period"], 1, 21600),
                tuple(expectations),
                tuple(supporting),
                MappingProxyType(investigation),
                revision,
            )
        )
        checks_per_day += (
            (86400 + schedule.period_seconds - 1) // schedule.period_seconds
        ) * len(expectations)

    if checks_per_day > 12000:
        raise InvalidDefinition(
            "Expected checks exceed 12,000 deliveries per day"
        )

    return Definition(consumer, MappingProxyType(sources), tuple(pipelines))


def approved_resources(
    config: dict[str, Any],
) -> Mapping[str, Mapping[str, object]]:
    raw = config.get("health_resources")

    if type(raw) is not dict or len(raw) > 32:
        raise InvalidDefinition(
            "Trusted health_resources configuration is required"
        )

    resources = {}

    for identifier, profile in raw.items():
        identifier = name(identifier)
        if type(profile) is not dict:
            raise InvalidDefinition("Invalid approved resource profile")

        adapter = name(profile.get("adapter"))

        if adapter not in ADAPTERS:
            raise InvalidDefinition("Unknown approved resource adapter")

        resources[identifier] = ADAPTERS[adapter].resource_profile(
            profile, config
        )

    return MappingProxyType(resources)


def bind(
    definition: Definition,
    config: dict[str, Any],
) -> Mapping[str, Mapping[str, object]]:
    """Bind declared sources to host-approved resource profiles."""
    resources = approved_resources(config)

    for source in definition.sources.values():
        if not definition.consumer.enabled:
            continue

        source.target(resources, "scope-probe", config["account_id"])

    return resources
