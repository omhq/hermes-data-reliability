from typing import TYPE_CHECKING, Any

from tool_boundary.providers.aws_identity import AwsIdentity
from tool_boundary.results import Evidence, selected_fields
from tool_boundary.validation import fields, require
from tool_boundary.wire import ExecutionError

from .aws import AwsToolProvider
from .base import CliRequest, Scope

if TYPE_CHECKING:
    from tool_boundary.contracts import ToolExtension
    from tool_boundary.models import ApprovedAction, ResolvedRead

QUEUE_ATTRIBUTES = (
    "QueueArn",
    "ApproximateNumberOfMessages",
    "ApproximateNumberOfMessagesNotVisible",
    "ApproximateNumberOfMessagesDelayed",
    "VisibilityTimeout",
    "DelaySeconds",
    "MessageRetentionPeriod",
    "MaximumMessageSize",
    "ReceiveMessageWaitTimeSeconds",
    "RedrivePolicy",
)


def queue_url(arn: str) -> str:
    _, _, _, region, account, name = arn.split(":")
    return f"https://sqs.{region}.amazonaws.com/{account}/{name}"


class SqsProvider(AwsToolProvider):
    package = "hermes-sqs"
    toolset = "aws_sqs"
    service = "sqs"
    modes = {
        "run": frozenset({"get_sqs_queue_attributes"}),
        "log": frozenset({"get_sqs_queue_attributes"}),
        "resources": frozenset(
            {"describe_sqs_scope", "get_sqs_queue_attributes"}
        ),
    }

    @classmethod
    def queue_arns(cls, configuration: dict[str, Any]) -> tuple[str, ...]:
        return AwsIdentity.resource_arns(
            configuration.get("sqs_queue_arns", []),
            (
                f"arn:aws:sqs:{configuration.get('region', '')}:"
                f"{configuration.get('account_id', '')}:"
            ),
            r"[A-Za-z0-9_-]{1,80}(?:\.fifo)?",
        )

    def __init__(
        self,
        configuration: dict[str, Any],
        extension: ToolExtension | None = None,
    ) -> None:
        super().__init__(configuration, extension)
        self.queues = self.queue_arns(configuration)

        if (
            self.package in configuration.get("tool_packages", [])
            and not self.queues
        ):
            raise ValueError("SQS tools require approved queue ARNs")

    def resources(self) -> dict[str, Any]:
        return {"sqs_queue_arns": list(self.queues)}

    def authorize(
        self,
        scope: Scope,
        tool: str,
        arguments: dict[str, Any],
        maximum: int,
        now: int,
    ) -> ResolvedRead:
        if tool == "describe_sqs_scope":
            return self._description(scope, arguments)

        fields(arguments, {"queue_arn"}, {"max_bytes"})
        arn = arguments["queue_arn"]
        require(
            type(arn) is str and arn in self.queues, "resource_not_allowed"
        )
        return (
            "sqs",
            "GetQueueAttributes",
            {
                "QueueUrl": queue_url(arn),
                "AttributeNames": list(QUEUE_ATTRIBUTES),
            },
            None,
            None,
        )

    def request(self, action: ApprovedAction) -> CliRequest:
        require(
            action.operation == "GetQueueAttributes", "invalid_execution_plan"
        )
        require(
            action.parameters.get("QueueUrl")
            in {queue_url(arn) for arn in self.queues},
            "invalid_execution_plan",
        )
        require(
            action.parameters_dict().get("AttributeNames")
            == list(QUEUE_ATTRIBUTES),
            "invalid_execution_plan",
        )
        return self.command(
            action,
            "get-queue-attributes",
            {"QueueUrl": "--queue-url", "AttributeNames": "--attribute-names"},
            json_fields=frozenset({"AttributeNames"}),
        )

    def normalize(
        self,
        action: ApprovedAction,
        response: dict[str, Any],
        body: bytes | None,
    ) -> Evidence:
        require(
            action.operation == "GetQueueAttributes", "invalid_execution_plan"
        )
        attributes = response.get("Attributes")
        arn = next(
            (
                arn
                for arn in self.queues
                if queue_url(arn) == action.parameters["QueueUrl"]
            ),
            None,
        )

        if (
            arn is None
            or type(attributes) is not dict
            or attributes.get("QueueArn") != arn
        ):
            raise ExecutionError("invalid_aws_response")

        selected = selected_fields(
            attributes, {name: str for name in QUEUE_ATTRIBUTES}
        )

        for name in QUEUE_ATTRIBUTES[1:-1]:
            value = selected.get(name)

            if value is None or not value.isascii() or not value.isdigit():
                raise ExecutionError("invalid_aws_response")

        return Evidence(
            {
                "queue_arn": arn,
                "attributes": selected,
                "limitation": (
                    "Queue counts are approximate current observations. "
                    "They do not prove receipt or processing of a particular "
                    "message."
                ),
            },
            [
                {
                    "reference": "aws-resource:" + arn,
                    "observation": "queue_attributes",
                }
            ],
        )
