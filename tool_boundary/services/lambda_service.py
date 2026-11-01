import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tool_boundary.models import PolicyDenied
from tool_boundary.providers.aws_identity import AwsIdentity
from tool_boundary.results import Evidence, selected_fields
from tool_boundary.validation import fields, require
from tool_boundary.wire import ExecutionError

from .aws import AwsToolProvider
from .base import CliRequest, Scope
from .sqs import SqsProvider

if TYPE_CHECKING:
    from tool_boundary.contracts import ToolExtension
    from tool_boundary.models import ApprovedAction, ResolvedRead

UUID_PATTERN = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"


@dataclass(frozen=True)
class EventSourceMapping:
    identifier: str
    function_arn: str
    queue_arn: str


class LambdaProvider(AwsToolProvider):
    package = "hermes-lambda"
    toolset = "aws_lambda"
    service = "lambda"
    modes = {
        "run": frozenset(
            {
                "get_lambda_function_configuration",
                "get_lambda_function_concurrency",
                "get_lambda_event_source_mapping",
            }
        ),
        "log": frozenset(
            {
                "get_lambda_function_configuration",
                "get_lambda_function_concurrency",
                "get_lambda_event_source_mapping",
            }
        ),
        "resources": frozenset(
            {
                "describe_lambda_scope",
                "get_lambda_function_configuration",
                "get_lambda_function_concurrency",
                "get_lambda_event_source_mapping",
            }
        ),
    }

    def __init__(
        self,
        configuration: dict[str, Any],
        extension: ToolExtension | None = None,
    ) -> None:
        super().__init__(configuration, extension)
        self.queues = SqsProvider.queue_arns(configuration)
        self.functions = AwsIdentity.resource_arns(
            configuration.get("lambda_function_arns", []),
            (
                f"arn:aws:lambda:{configuration.get('region', '')}:"
                f"{configuration.get('account_id', '')}:function:"
            ),
            r"[A-Za-z0-9_-]{1,64}",
        )
        raw = configuration.get("lambda_event_source_mappings", {})

        if type(raw) is not dict or len(raw) > 50:
            raise ValueError("Invalid event-source mapping allowlist")

        mappings = []

        for identifier, binding in raw.items():
            if (
                type(identifier) is not str
                or not re.fullmatch(UUID_PATTERN, identifier)
                or type(binding) is not dict
                or set(binding) != {"function_arn", "queue_arn"}
                or binding["function_arn"] not in self.functions
                or binding["queue_arn"] not in self.queues
            ):
                raise ValueError("Invalid event-source mapping allowlist")

            mappings.append(
                EventSourceMapping(
                    identifier, binding["function_arn"], binding["queue_arn"]
                )
            )

        self.mappings = tuple(mappings)

        if (
            self.package in configuration.get("tool_packages", [])
            and not self.functions
        ):
            raise ValueError("Lambda tools require approved function ARNs")

    def mapping(self, identifier: object) -> EventSourceMapping:
        for binding in self.mappings:
            if binding.identifier == identifier:
                return binding

        raise PolicyDenied("resource_not_allowed")

    def resources(self) -> dict[str, Any]:
        return {
            "lambda_function_arns": list(self.functions),
            "lambda_event_source_mappings": {
                binding.identifier: {
                    "function_arn": binding.function_arn,
                    "queue_arn": binding.queue_arn,
                }
                for binding in self.mappings
            },
            "sqs_queue_arns": list(self.queues),
        }

    def authorize(
        self,
        scope: Scope,
        tool: str,
        arguments: dict[str, Any],
        maximum: int,
        now: int,
    ) -> ResolvedRead:
        if tool == "describe_lambda_scope":
            return self._description(scope, arguments)
        if tool == "get_lambda_event_source_mapping":
            fields(arguments, {"mapping_id"}, {"max_bytes"})
            binding = self.mapping(arguments["mapping_id"])
            return (
                "lambda",
                "GetEventSourceMapping",
                {"UUID": binding.identifier},
                None,
                None,
            )

        fields(arguments, {"function_arn"}, {"max_bytes"})
        arn = arguments["function_arn"]
        require(
            type(arn) is str and arn in self.functions, "resource_not_allowed"
        )
        operations = {
            "get_lambda_function_configuration": "GetFunctionConfiguration",
            "get_lambda_function_concurrency": "GetFunctionConcurrency",
        }
        require(tool in operations, "unknown_tool")
        return "lambda", operations[tool], {"FunctionName": arn}, None, None

    def request(self, action: ApprovedAction) -> CliRequest:
        operations = {
            "GetFunctionConfiguration": "get-function-configuration",
            "GetFunctionConcurrency": "get-function-concurrency",
        }

        if action.operation == "GetEventSourceMapping":
            self.mapping(action.parameters.get("UUID"))
            return self.command(
                action, "get-event-source-mapping", {"UUID": "--uuid"}
            )

        require(
            action.operation in operations
            and action.parameters.get("FunctionName") in self.functions,
            "invalid_execution_plan",
        )
        return self.command(
            action,
            operations[action.operation],
            {"FunctionName": "--function-name"},
        )

    def normalize(
        self,
        action: ApprovedAction,
        response: dict[str, Any],
        body: bytes | None,
    ) -> Evidence:
        data = self._projection(action, response)
        field, observation = {
            "GetFunctionConfiguration": (
                "function_arn",
                "lambda_configuration",
            ),
            "GetFunctionConcurrency": ("function_arn", "lambda_concurrency"),
            "GetEventSourceMapping": (
                "mapping_arn",
                "lambda_event_source_mapping",
            ),
        }[action.operation]
        return Evidence(
            data,
            [
                {
                    "reference": "aws-resource:" + data[field],
                    "observation": observation,
                }
            ],
        )

    def _projection(
        self, action: ApprovedAction, response: dict[str, Any]
    ) -> dict[str, Any]:
        if action.operation == "GetFunctionConcurrency":
            reserved = response.get("ReservedConcurrentExecutions")

            if "ReservedConcurrentExecutions" in response and (
                type(reserved) is not int or reserved < 0
            ):
                raise ExecutionError("invalid_aws_response")

            return {
                "function_arn": action.parameters["FunctionName"],
                "reserved_concurrent_executions": reserved,
                "limitation": (
                    "This is the current reserved-concurrency configuration, "
                    "not invocation or throttle history. No reservation does "
                    "not imply unlimited available concurrency."
                ),
            }

        if action.operation == "GetFunctionConfiguration":
            arn = action.parameters["FunctionName"]

            if response.get("FunctionArn") != arn:
                raise ExecutionError("invalid_aws_response")

            configuration = selected_fields(
                response,
                {
                    "Runtime": str,
                    "Handler": str,
                    "Timeout": int,
                    "MemorySize": int,
                    "State": str,
                    "StateReasonCode": str,
                    "LastUpdateStatus": str,
                    "LastUpdateStatusReasonCode": str,
                    "PackageType": str,
                    "Version": str,
                    "LastModified": str,
                    "CodeSha256": str,
                    "RevisionId": str,
                },
            )
            return {
                "function_arn": arn,
                "configuration": configuration,
                "limitation": (
                    "Current configuration does not establish historical "
                    "state or processing of a particular message. "
                    "Environment values, "
                    "code, and unselected settings are withheld."
                ),
            }

        if action.operation != "GetEventSourceMapping":
            raise ExecutionError("invalid_execution_plan")

        binding = self.mapping(action.parameters["UUID"])

        if (
            response.get("UUID") != binding.identifier
            or response.get("FunctionArn") != binding.function_arn
            or response.get("EventSourceArn") != binding.queue_arn
        ):
            raise ExecutionError("invalid_aws_response")

        configuration = selected_fields(
            response,
            {
                "State": str,
                "StateTransitionReason": str,
                "LastProcessingResult": str,
                "BatchSize": int,
                "MaximumBatchingWindowInSeconds": int,
            },
        )
        return {
            "mapping_arn": (
                f"arn:aws:lambda:{action.region}:{action.account_id}:"
                f"event-source-mapping:{binding.identifier}"
            ),
            "mapping_id": binding.identifier,
            "function_arn": binding.function_arn,
            "queue_arn": binding.queue_arn,
            "configuration": configuration,
            "limitation": (
                "Current mapping settings do not establish that a particular "
                "message was delivered or processed."
            ),
        }
