import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import Any

from tool_boundary.models import ApprovedAction
from tool_boundary.services.s3 import listing_result
from tool_boundary.wire import ExecutionError

from ..observations import Observation, ReadEvidence, Target
from ..validation import InvalidDefinition, mapping


class S3ObjectAdapter:
    name = "s3_object"
    supporting_tools = frozenset(
        {"search_log_events", "read_s3_object", "list_s3_objects"}
    )

    def source_options(self, value: object) -> Mapping[str, object]:
        options = mapping(value, {"key_template"})
        template = options["key_template"]

        if (
            type(template) is not str
            or template.count("{delivery_id}") != 1
            or not re.fullmatch(r"[A-Za-z0-9_./{}:-]{1,900}", template)
            or template.startswith("/")
            or template.endswith("/")
            or any(part in {".", "..", ""} for part in template.split("/"))
            or "{" in template.replace("{delivery_id}", "")
            or "}" in template.replace("{delivery_id}", "")
        ):
            raise InvalidDefinition("Use a relative key with {delivery_id}")

        return MappingProxyType(options)

    def resource_profile(
        self,
        value: object,
        config: dict[str, Any],
    ) -> Mapping[str, object]:
        profile = mapping(value, {"adapter", "bucket", "region", "prefix"})

        if (
            profile["adapter"] != self.name
            or profile["bucket"] != config["bucket"]
            or profile["region"] != config["region"]
            or type(profile["prefix"]) is not str
        ):
            raise InvalidDefinition(
                "Evidence profile exceeds the installed AWS scope"
            )

        self.resolve(
            "validation",
            profile,
            {"key_template": "{delivery_id}"},
            "scope-probe",
            config["account_id"],
        )
        return MappingProxyType(profile)

    def resolve(
        self,
        resource: str,
        profile: Mapping[str, object],
        options: Mapping[str, object],
        delivery_id: str,
        account: str,
    ) -> Target:
        values = mapping(
            dict(profile), {"adapter", "bucket", "region", "prefix"}
        )

        if values["adapter"] != self.name or any(
            type(values[field]) is not str
            for field in ("bucket", "region", "prefix")
        ):
            raise InvalidDefinition("Invalid approved S3 resource")

        prefix = str(values["prefix"])
        template = options["key_template"]

        if type(template) is not str:
            raise InvalidDefinition("Invalid S3 key template")

        key = prefix + template.replace("{delivery_id}", delivery_id)

        if (
            not prefix.endswith("/")
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:-]{0,1023}", key)
            or any(part in {".", "..", ""} for part in key.split("/"))
        ):
            raise InvalidDefinition("Invalid resolved object key")

        scope = hashlib.sha256(
            json.dumps({"account": account, **values}, sort_keys=True).encode()
        ).hexdigest()
        return Target(
            self.name,
            resource,
            scope,
            {
                "bucket": str(values["bucket"]),
                "region": str(values["region"]),
                "key": key,
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
        key = target.location["key"]
        listing = replace(
            action,
            service="s3",
            operation="ListObjectsV2",
            parameters={
                "Bucket": target.location["bucket"],
                "Prefix": key,
                "MaxKeys": 20,
            },
            key_prefix=key,
            exact_key=None,
        )
        limitation = (
            "Presence does not establish content validity, completeness, "
            "original input identity, or arrival before the deadline."
        )

        try:
            response = read(listing)
            if type(response) is not dict:
                raise ExecutionError("invalid_aws_response")

            data, partial = listing_result(listing, response)
        except ExecutionError:
            return Observation(
                {"exists": None},
                "unavailable",
                "object_listing_unavailable",
                limitation,
            )

        exists = any(item["key"] == key for item in data["objects"])
        return Observation(
            {"exists": True if exists else (None if partial else False)},
            "partial" if partial else "complete",
            "object_listing_incomplete"
            if partial
            else "object_listing_complete",
            limitation,
        )

    def constrain_read(
        self, target: Target, action: ApprovedAction
    ) -> ApprovedAction:
        if action.operation == "ListObjectsV2":
            return replace(
                action,
                exact_key=str(action.parameters["Prefix"]),
                key_prefix=None,
            )

        return action

    def permits_read(
        self,
        target: Target,
        tool: str,
        arguments: dict[str, Any],
    ) -> bool:
        if tool == "search_log_events":
            return True

        field = "key" if tool == "read_s3_object" else "prefix"
        return (
            tool in self.supporting_tools
            and arguments.get("bucket") == target.location["bucket"]
            and arguments.get(field) == target.location["key"]
        )
