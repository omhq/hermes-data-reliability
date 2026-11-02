from typing import Any

from aws_access.configuration import CredentialError
from aws_access.provider import CredentialProvider
from collector.execution import CollectionError, Deadline

from .identity import AwsIdentity
from .process import CliRunner, run_json


class AwsClient:
    """Host collection access, independent of agent tool authorization."""

    def __init__(
        self, identity: AwsIdentity, runner: CliRunner = run_json
    ) -> None:
        self.identity = identity
        self.credentials = CredentialProvider(identity.authentication)
        self.runner = runner
        self.environment: dict[str, str] | None = None

    def _call(
        self,
        service: str,
        operation: str,
        arguments: list[str],
        environment: dict[str, str],
        deadline: Deadline,
    ) -> dict[str, Any]:
        return self.runner(
            [
                "/usr/bin/aws",
                service,
                operation,
                "--region",
                self.identity.region,
                "--output",
                "json",
                "--no-cli-pager",
                "--no-paginate",
                "--cli-connect-timeout",
                "5",
                "--cli-read-timeout",
                "20",
                *arguments,
            ],
            environment,
            deadline,
        )

    def _authenticate(self, deadline: Deadline) -> dict[str, str]:
        def call(
            command: list[str], environment: dict[str, str]
        ) -> dict[str, Any]:
            return self.runner(command, environment, deadline)

        try:
            return self.credentials.authenticate(call).environment
        except CredentialError as error:
            raise CollectionError(error.code) from None

    def call(
        self,
        service: str,
        operation: str,
        arguments: list[str],
        deadline: Deadline,
    ) -> dict[str, Any]:
        if self.environment is None:
            self.environment = self._authenticate(deadline)

        return self._call(
            service, operation, arguments, self.environment, deadline
        )
