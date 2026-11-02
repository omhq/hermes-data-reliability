import re
from typing import TYPE_CHECKING, Any

from tool_boundary.models import ResourceContext
from tool_boundary.results import Evidence, fit_items, integer_parameter
from tool_boundary.validation import fields, integer, require
from tool_boundary.wire import ExecutionError, encode

from .aws import AwsToolProvider
from .base import CliRequest, Scope

if TYPE_CHECKING:
    from tool_boundary.contracts import ToolExtension
    from tool_boundary.models import ApprovedAction, ResolvedRead


class S3Provider(AwsToolProvider):
    package = "hermes-lab-aws"
    toolset = "hermes_lab_aws"
    service = "s3"
    modes = {
        "run": frozenset({"list_run_objects", "read_run_object"}),
        "resources": frozenset(
            {"list_aws_resources", "list_s3_objects", "read_s3_object"}
        ),
    }

    def __init__(
        self,
        configuration: dict[str, Any],
        extension: ToolExtension | None = None,
    ) -> None:
        super().__init__(configuration, extension)
        bucket = configuration.get("bucket")

        if not self.valid_bucket(bucket):
            raise ValueError("Invalid approved S3 bucket")

    @staticmethod
    def valid_bucket(value: object) -> bool:
        return (
            type(value) is str
            and re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", value)
            is not None
        )

    def validate_scope(self, scope: Scope) -> None:
        super().validate_scope(scope)
        context = scope.context
        require(
            self.valid_bucket(context.bucket)
            and context.bucket == self.configuration["bucket"],
            "invalid_context",
        )

        if isinstance(context, ResourceContext):
            require(
                type(context.prefixes) is tuple
                and all(type(prefix) is str for prefix in context.prefixes),
                "invalid_context",
            )

            if context.profile == "lab":
                require(
                    context.prefixes == ("input/", "output/", "runs/"),
                    "invalid_context",
                )

    def resources(self) -> dict[str, Any]:
        return {
            "s3": [
                {
                    "bucket": self.configuration["bucket"],
                    "prefixes": ["input/", "output/", "runs/"],
                }
            ]
        }

    def describe(self, scope: Scope) -> dict[str, Any]:
        result = super().describe(scope)

        if self.catalog is not None:
            for name in self.catalog.selection.enabled:
                provider = self.catalog.package(name)

                if provider is not self:
                    result.update(provider.describe(scope))

            result["enabled_tools"] = sorted(self.catalog.selection.tools)

        if isinstance(scope.context, ResourceContext):
            result["s3"] = [
                {
                    "bucket": scope.context.bucket,
                    "prefixes": list(scope.context.prefixes),
                }
            ]

        return result

    def authorize(
        self,
        scope: Scope,
        tool: str,
        arguments: dict[str, Any],
        maximum: int,
        now: int,
    ) -> ResolvedRead:
        if tool == "list_aws_resources":
            return self._description(scope, arguments)
        if scope.mode == "run":
            context = scope.incident()
            require(type(arguments.get("run_id")) is str, "invalid_arguments")
            require(arguments["run_id"] == context.run_id, "wrong_incident")

            if tool == "list_run_objects":
                return self._run_listing(scope, arguments)

            return self._run_object(scope, arguments)

        return self._s3(scope, tool, arguments, maximum)

    def _run_listing(
        self, scope: Scope, arguments: dict[str, Any]
    ) -> ResolvedRead:
        fields(arguments, {"run_id", "kind"}, {"max_keys", "max_bytes"})

        context = scope.incident()
        kind = arguments["kind"]

        require(
            type(kind) is str and kind in {"input", "output", "runs"},
            "invalid_object_kind",
        )

        count = integer(
            arguments.get("max_keys", scope.limits.max_keys),
            1,
            scope.limits.max_keys,
            "invalid_key_limit",
        )
        exact_key = None
        key_prefix = None

        if kind == "runs":
            prefix = f"runs/{context.run_id}/"
            key_prefix = prefix
        else:
            prefix = f"{kind}/{context.run_id}.json"
            exact_key = prefix

        return (
            "s3",
            "ListObjectsV2",
            {
                "Bucket": context.bucket,
                "Prefix": prefix,
                "MaxKeys": count,
            },
            exact_key,
            key_prefix,
        )

    def _run_object(
        self, scope: Scope, arguments: dict[str, Any]
    ) -> ResolvedRead:
        fields(arguments, {"run_id", "kind"}, {"attempt_id", "max_bytes"})

        context = scope.incident()
        kind = arguments["kind"]

        require(
            type(kind) is str and kind in {"input", "output", "log"},
            "invalid_object_kind",
        )

        if kind == "log":
            attempt = arguments.get("attempt_id")
            require(
                type(attempt) is str and attempt in context.attempt_ids,
                "unapproved_attempt",
            )

            key = f"runs/{context.run_id}/{attempt}.json"
        else:
            require("attempt_id" not in arguments, "unexpected_arguments")
            key = f"{kind}/{context.run_id}.json"

        max_bytes = arguments.get("max_bytes", scope.limits.default_bytes)

        return (
            "s3",
            "GetObject",
            {
                "Bucket": context.bucket,
                "Key": key,
                "Range": f"bytes=0-{max_bytes - 1}",
            },
            key,
            None,
        )

    def _s3(
        self, scope: Scope, tool: str, arguments: dict[str, Any], maximum: int
    ) -> ResolvedRead:
        context = scope.resources()
        listing = tool == "list_s3_objects"
        key_field = "prefix" if listing else "key"
        optional = {"max_bytes", "max_keys"} if listing else {"max_bytes"}

        fields(arguments, {"bucket", key_field}, optional)
        require(arguments["bucket"] == context.bucket, "resource_not_allowed")

        key = arguments[key_field]

        require(
            type(key) is str
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:-]{0,1023}", key)
            is not None,
            "invalid_object_key",
        )
        require(
            not any(part in {".", ".."} for part in key.split("/"))
            and "//" not in key,
            "invalid_object_key",
        )
        require(
            any(key.startswith(prefix) for prefix in context.prefixes),
            "resource_not_allowed",
        )

        if listing:
            count = integer(
                arguments.get("max_keys", 20),
                1,
                scope.limits.max_keys,
                "invalid_key_limit",
            )

            return (
                "s3",
                "ListObjectsV2",
                {
                    "Bucket": context.bucket,
                    "Prefix": key,
                    "MaxKeys": count,
                },
                None,
                key,
            )

        require(not key.endswith("/"), "invalid_object_key")

        return (
            "s3",
            "GetObject",
            {
                "Bucket": context.bucket,
                "Key": key,
                "Range": f"bytes=0-{maximum - 1}",
            },
            key,
            None,
        )

    def request(self, action: ApprovedAction) -> CliRequest:
        parameters = action.parameters
        require(
            parameters.get("Bucket") == self.configuration["bucket"],
            "invalid_execution_plan",
        )
        fixed = ("--expected-bucket-owner", action.account_id)

        if action.operation == "GetObject":
            require(
                action.exact_key == parameters.get("Key"),
                "invalid_execution_plan",
            )
            require(
                parameters.get("Range") == f"bytes=0-{action.max_bytes - 1}",
                "invalid_execution_plan",
            )
            return self.command(
                action,
                "get-object",
                {"Bucket": "--bucket", "Key": "--key", "Range": "--range"},
                fixed=fixed,
                body=True,
                cli_service="s3api",
            )
        if action.operation == "ListObjectsV2":
            require(
                action.exact_key == parameters.get("Prefix")
                or action.key_prefix == parameters.get("Prefix"),
                "invalid_execution_plan",
            )
            return self.command(
                action,
                "list-objects-v2",
                {
                    "Bucket": "--bucket",
                    "Prefix": "--prefix",
                    "MaxKeys": "--max-keys",
                },
                fixed=fixed,
                cli_service="s3api",
            )

        raise ExecutionError("invalid_execution_plan")

    def normalize(
        self,
        action: ApprovedAction,
        response: dict[str, Any],
        body: bytes | None,
    ) -> Evidence:
        if action.operation == "GetObject":
            data, partial = object_result(action, response, body)
            return Evidence(
                data,
                [
                    {
                        "reference": f"s3://{action.parameters['Bucket']}/{action.parameters['Key']}",
                        "observation": "object_sample",
                    }
                ],
                partial,
            )
        if action.operation != "ListObjectsV2":
            raise ExecutionError("invalid_execution_plan")

        data, partial = listing_result(action, response)
        references = [
            {
                "reference": f"s3://{action.parameters['Bucket']}/{item['key']}",
                "observation": "object_listed",
            }
            for item in data["objects"]
        ]

        if action.exact_key and not references and not partial:
            references.append(
                {
                    "reference": f"s3://{action.parameters['Bucket']}/{action.exact_key}",
                    "observation": "object_absent",
                }
            )

        return Evidence(data, references, partial, "objects")


def object_result(
    action: ApprovedAction, metadata: dict[str, Any], body: bytes | None
) -> tuple[dict[str, str], bool]:
    if type(body) is not bytes or len(body) > action.max_bytes:
        raise ExecutionError("invalid_aws_response")

    text = body.decode("utf-8", errors="replace")

    if len(encode({"text": ""})) > action.max_bytes:
        raise ExecutionError("response_budget_too_small")

    low, high = 0, len(text)

    while low < high:
        middle = (low + high + 1) // 2

        if len(encode({"text": text[:middle]})) <= action.max_bytes:
            low = middle
        else:
            high = middle - 1

    content_range = metadata.get("ContentRange", "")
    match = re.fullmatch(r"bytes [0-9]+-([0-9]+)/([0-9]+)", content_range)
    partial = (
        int(match[1]) + 1 < int(match[2])
        if match
        else len(body) >= action.max_bytes
    )

    # Unknown total length at the range limit is conservatively partial.
    return {"text": text[:low]}, partial or low < len(text)


def listing_result(
    action: ApprovedAction, response: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    contents = response.get("Contents", [])

    if (
        type(contents) is not list
        or type(response.get("IsTruncated", False)) is not bool
    ):
        raise ExecutionError("invalid_aws_response")

    for field, expected in (
        ("Name", action.parameters["Bucket"]),
        ("Prefix", action.parameters["Prefix"]),
    ):
        if field in response and response[field] != expected:
            raise ExecutionError("invalid_aws_response")

    if "KeyCount" in response and (
        type(response["KeyCount"]) is not int
        or response["KeyCount"] != len(contents)
    ):
        raise ExecutionError("invalid_aws_response")

    scope = action.exact_key or action.key_prefix

    if scope is None:
        raise ExecutionError("invalid_execution_plan")

    maximum = integer_parameter(action, "MaxKeys", 1)
    items = []
    filtered = False

    for entry in contents:
        if type(entry) is not dict:
            raise ExecutionError("invalid_aws_response")

        key = entry.get("Key")
        scoped = type(key) is str and (
            key == scope if action.exact_key else key.startswith(scope)
        )

        if type(key) is not str or not scoped:
            filtered = True
            continue

        if (
            len(key) > 1024
            or type(entry.get("Size")) is not int
            or entry["Size"] < 0
        ):
            raise ExecutionError("invalid_aws_response")

        items.append({"key": key, "size": entry["Size"]})

    data, partial = fit_items("objects", items[:maximum], action.max_bytes)

    # Missing completion metadata cannot establish absence of an object.
    complete = (
        response.get("IsTruncated") is False
        and type(response.get("KeyCount")) is int
        and response["KeyCount"] == len(contents)
        and response.get("Name") == action.parameters["Bucket"]
        and response.get("Prefix") == action.parameters["Prefix"]
        and not response.get("CommonPrefixes")
    )

    return data, (
        partial
        or filtered
        or len(items) > maximum
        or not complete
        or bool(response.get("NextContinuationToken"))
    )
