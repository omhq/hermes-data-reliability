import json
import math
import os
import re
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Any, NoReturn, Protocol

from aws_access.configuration import AccessPolicy, CredentialError
from aws_access.provider import CredentialProvider


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}

    for key, value in pairs:
        if key in result:
            raise ValueError("Invalid JSON")

        result[key] = value

    return result


def _reject_constant(value: str) -> NoReturn:
    raise ValueError("Invalid JSON")


def _finite_float(value: str) -> float:
    result = float(value)

    if not math.isfinite(result):
        raise ValueError("Invalid JSON")

    return result


def _decode(payload: str | bytes | bytearray) -> Any:
    """Parse external JSON without ambiguous fields or raw error payloads."""
    try:
        return json.loads(
            payload,
            object_pairs_hook=_unique_fields,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except ValueError, UnicodeError, RecursionError:
        raise ValueError("Invalid JSON") from None


class AwsCallError(RuntimeError):
    """An AWS error code that excludes provider output."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class Deadline:
    def __init__(self, seconds: float) -> None:
        self.end = time.monotonic() + seconds

    def remaining(self) -> float:
        remaining = self.end - time.monotonic()

        if remaining <= 0:
            raise AwsCallError("execution_timeout")

        return remaining


class CliRunner(Protocol):
    """AWS CLI execution callable for the client and test doubles."""

    def __call__(
        self,
        command: list[str],
        environment: dict[str, str],
        deadline: Deadline,
    ) -> dict[str, Any]: ...


def run_json(
    command: list[str],
    environment: dict[str, str],
    deadline: Deadline,
) -> dict[str, Any]:
    """Run an AWS call with bounded time, output, and safe errors."""
    deadline.remaining()

    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    caps = {"stdout": 2 * 1024 * 1024, "stderr": 16 * 1024}

    with subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        shell=False,
        start_new_session=True,
    ) as process:
        failed = True

        try:
            stdout, stderr = process.stdout, process.stderr

            if stdout is None or stderr is None:
                raise AwsCallError("aws_failure")

            with selectors.DefaultSelector() as selector:
                selector.register(stdout, selectors.EVENT_READ, "stdout")
                selector.register(stderr, selectors.EVENT_READ, "stderr")

                while selector.get_map() or process.poll() is None:
                    remaining = deadline.remaining()

                    for key, _ in selector.select(min(remaining, 0.05)):
                        chunk = os.read(key.fd, 65536)

                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue

                        buffer = buffers[key.data]

                        if len(buffer) + len(chunk) > caps[key.data]:
                            raise AwsCallError("upstream_response_too_large")

                        buffer.extend(chunk)

            deadline.remaining()

            if process.returncode:
                # Interpret only known codes. Never return raw stderr.
                stderr = bytes(buffers["stderr"])
                for marker, code in (
                    (b"(AccessDenied", "access_denied"),
                    (b"(NoSuchKey)", "not_found"),
                    (b"(ExpiredToken", "credentials_expired"),
                    (b"(Throttling", "throttled"),
                ):
                    if marker in stderr:
                        raise AwsCallError(code)

                raise AwsCallError("aws_failure")

            try:
                response = _decode(buffers["stdout"])
            except ValueError:
                raise AwsCallError("invalid_aws_response") from None

            if type(response) is not dict:
                raise AwsCallError("invalid_aws_response")

            failed = False

            return response
        finally:
            # A child can keep a pipe open after the CLI parent exits.
            if failed or process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

            process.wait()


@dataclass(frozen=True)
class NotificationConfig:
    account_id: str
    region: str
    topic_arn: str
    authentication: AccessPolicy

    @classmethod
    def load(cls, operations: dict[str, Any]) -> NotificationConfig:
        account = operations.get("account_id")
        region = operations.get("region")
        topic = operations.get("alert_topic_arn")
        authentication = AccessPolicy.load(operations)

        if (
            type(account) is not str
            or not re.fullmatch(r"[0-9]{12}", account)
            or type(region) is not str
            or not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]+", region)
            or type(topic) is not str
            or not re.fullmatch(
                rf"arn:aws:sns:{region}:{account}:[A-Za-z0-9_-]+", topic
            )
        ):
            raise ValueError("Invalid trusted notification configuration")

        return cls(account, region, topic, authentication)


class Publisher:
    def __init__(
        self, config: NotificationConfig, runner: CliRunner = run_json
    ) -> None:
        self.config = config
        self.runner = runner
        self.credentials = CredentialProvider(config.authentication)

    def call(
        self,
        service: str,
        operation: str,
        arguments: list[str],
        deadline: Deadline,
        environment: dict[str, str],
    ) -> dict[str, Any]:
        return self.runner(
            [
                "/usr/bin/aws",
                service,
                operation,
                "--region",
                self.config.region,
                "--output",
                "json",
                "--no-cli-pager",
                "--cli-connect-timeout",
                "5",
                "--cli-read-timeout",
                "15",
                *arguments,
            ],
            environment,
            deadline,
        )

    def publish(self, subject: str, message: str) -> str:
        deadline = Deadline(40)

        def call(
            command: list[str], environment: dict[str, str]
        ) -> dict[str, Any]:
            return self.runner(command, environment, deadline)

        try:
            environment = self.credentials.authenticate(call).environment
        except CredentialError as error:
            raise AwsCallError(error.code) from None

        result = self.call(
            "sns",
            "publish",
            [
                "--topic-arn",
                self.config.topic_arn,
                "--subject",
                subject,
                "--message",
                message,
            ],
            deadline,
            environment,
        )
        message_id = result.get("MessageId")

        if type(message_id) is not str or not message_id:
            raise AwsCallError("invalid_publish_response")

        return message_id
