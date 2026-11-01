import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import Any

from tool_boundary.models import ApprovedAction
from tool_boundary.wire import ExecutionError

from ..observations import Observation, ReadEvidence, Target
from ..validation import InvalidDefinition, duration, mapping


class CollectedEventsAdapter:
    name = "collected_events"
    supporting_tools = frozenset({"search_log_events"})

    def source_options(self, value: object) -> Mapping[str, object]:
        options = mapping(
            value,
            {
                "correlation_field",
                "event_field",
                "event_value",
                "settle_delay",
            },
        )

        for field in ("correlation_field", "event_field"):
            field_name = options[field]

            if type(field_name) is not str or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]{0,79}", field_name
            ):
                raise InvalidDefinition("Use a top-level JSON field name")

        event = options["event_value"]

        if type(event) is not str or not 1 <= len(event) <= 256:
            raise InvalidDefinition("Use a nonempty event value")

        duration(options["settle_delay"], 1, 3600)
        return MappingProxyType(options)

    def resource_profile(
        self, value: object, config: dict[str, Any]
    ) -> Mapping[str, object]:
        profile = mapping(
            value,
            {
                "adapter",
                "source_key",
                "connector",
                "account",
                "region",
                "location",
                "overlap",
            },
        )

        if (
            profile["adapter"] != self.name
            or profile["account"] != config["account_id"]
            or profile["region"] != config["region"]
            or profile["connector"] not in {"cloudwatch_logs", "cloudtrail"}
            or type(profile["source_key"]) is not str
            or not re.fullmatch(r"[a-f0-9]{64}", profile["source_key"])
            or type(profile["location"]) is not str
            or type(profile["overlap"]) is not int
        ):
            raise InvalidDefinition("Invalid collected evidence profile")

        return MappingProxyType(profile)

    def resolve(
        self,
        resource: str,
        profile: Mapping[str, object],
        options: Mapping[str, object],
        delivery_id: str,
        account: str,
    ) -> Target:
        settle = duration(options["settle_delay"], 1, 3600)
        overlap = profile["overlap"]

        if (
            type(overlap) is not int
            or account != profile["account"]
            or settle > overlap
        ):
            raise InvalidDefinition(
                "Collection overlap must cover settle_delay"
            )

        scope = hashlib.sha256(
            json.dumps(
                {
                    key: value
                    for key, value in profile.items()
                    if key != "overlap"
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return Target(
            self.name,
            resource,
            scope,
            {
                "source_key": str(profile["source_key"]),
                "connector": str(profile["connector"]),
                "region": str(profile["region"]),
                "location": str(profile["location"]),
                "correlation_value": delivery_id,
                **{key: str(value) for key, value in options.items()},
            },
        )

    def observe(
        self,
        target: Target,
        action: ApprovedAction,
        read: ReadEvidence,
        *,
        start: int,
        end: int,
        now: int,
    ) -> Observation:
        limitation = (
            "This checks a configured JSON event in collected evidence. "
            "It does not prove that a queue message was consumed, a file "
            "loaded correctly, or a record never occurred outside coverage."
        )
        allowance = duration(target.location["settle_delay"], 1, 3600) * 1000

        if now < end + allowance:
            return Observation(
                {"exists": None, "waiting_for_collection": True},
                "unavailable",
                "event_ingestion_allowance_not_elapsed",
                limitation,
            )

        query = replace(
            action,
            service="observations",
            operation="EventPresence",
            parameters={
                "source": target.location["source_key"],
                "start": start,
                "end": end,
                "correlation_field": target.location["correlation_field"],
                "correlation_value": target.location["correlation_value"],
                "event_field": target.location["event_field"],
                "event_value": target.location["event_value"],
                "settle_seconds": duration(
                    target.location["settle_delay"], 1, 3600
                ),
            },
        )

        try:
            result = read(query)
        except ExecutionError:
            return Observation(
                {"exists": None},
                "unavailable",
                "collected_evidence_unavailable",
                limitation,
            )

        if result["exists"] is True:
            return Observation(
                {
                    "exists": True,
                    "event_id": result.get("event_id"),
                    "event_timestamp_ms": result.get("event_timestamp_ms"),
                },
                result["status"],
                "configured_event_found",
                limitation,
            )

        complete = result["status"] == "complete"
        return Observation(
            {"exists": False if complete else None},
            "complete" if complete else "partial",
            "configured_event_not_found" if complete else result["reason"],
            limitation,
        )

    def constrain_read(
        self, target: Target, action: ApprovedAction
    ) -> ApprovedAction:
        return action

    def permits_read(
        self, target: Target, tool: str, arguments: dict[str, Any]
    ) -> bool:
        return (
            tool == "search_log_events"
            and target.location["connector"] == "cloudwatch_logs"
            and arguments.get("log_group") == target.location["location"]
        )
