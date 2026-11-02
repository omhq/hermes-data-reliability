import copy
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aws_access.configuration import AccessPolicy, CredentialError
from aws_access.provider import CredentialProvider
from tool_boundary.loader import load_tools
from tool_boundary.models import PolicyDenied
from tool_boundary.process import run_json
from tool_boundary.services.base import CliRequest
from tool_boundary.wire import ExecutionError

from .aws_identity import AwsIdentity

if TYPE_CHECKING:
    from tool_boundary.models import ApprovedAction
    from tool_boundary.process import CliRunner, Deadline


class AwsBackend:
    def __init__(
        self, config: dict[str, Any], runner: CliRunner = run_json
    ) -> None:
        AwsIdentity.validate_configuration(config)
        self.config = copy.deepcopy(config)
        self.providers = load_tools(self.config)
        self.runner = runner
        self.credentials = CredentialProvider(AccessPolicy.load(config))

    def _call(
        self,
        request: CliRequest,
        environment: dict[str, str],
        deadline: Deadline,
        *,
        body: Path | None = None,
        body_limit: int = 0,
    ) -> dict[str, Any]:
        command = [
            "/usr/bin/aws",
            request.service,
            request.operation,
            "--region",
            self.config["region"],
            "--output",
            "json",
            "--no-cli-pager",
            "--no-paginate",
            "--cli-connect-timeout",
            "5",
            "--cli-read-timeout",
            "20",
            *request.arguments,
        ]

        if body is not None:
            command.append(str(body))

        return self.runner(
            command, environment, deadline, body=body, body_limit=body_limit
        )

    def authenticate(self, deadline: Deadline) -> dict[str, str]:
        def call(
            command: list[str], environment: dict[str, str]
        ) -> dict[str, Any]:
            return self.runner(command, environment, deadline)

        try:
            return self.credentials.authenticate(call).environment
        except CredentialError as error:
            raise ExecutionError(error.code) from None

    def execute(
        self,
        action: ApprovedAction,
        environment: dict[str, str],
        deadline: Deadline,
    ) -> tuple[dict[str, Any], bytes | None]:
        if (
            action.account_id != self.config["account_id"]
            or action.region != self.config["region"]
            or action.max_pages != 1
        ):
            raise ExecutionError("invalid_execution_plan")

        try:
            request = self.providers.service(action.service).request(action)
        except PolicyDenied:
            raise ExecutionError("invalid_execution_plan") from None

        if not request.body:
            return self._call(request, environment, deadline), None

        with tempfile.TemporaryDirectory(
            prefix="hermes-lab-read-"
        ) as directory:
            body = Path(directory) / "body"
            metadata = self._call(
                request,
                environment,
                deadline,
                body=body,
                body_limit=action.max_bytes,
            )

            with body.open("rb") as source:
                sample = source.read(action.max_bytes + 1)

            if len(sample) > action.max_bytes:
                raise ExecutionError("upstream_response_too_large")

            return metadata, sample
