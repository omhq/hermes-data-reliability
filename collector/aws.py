import json
import math
import os
import selectors
import signal
import subprocess
import time
from typing import TYPE_CHECKING, Any, NoReturn, Protocol

if TYPE_CHECKING:
    from .access import CollectionAccess


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


def host_environment(region: str) -> dict[str, str]:
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": "/nonexistent",
        "AWS_CONFIG_FILE": "/dev/null",
        "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
        "AWS_EC2_METADATA_DISABLED": "false",
        "AWS_METADATA_SERVICE_TIMEOUT": "2",
        "AWS_METADATA_SERVICE_NUM_ATTEMPTS": "1",
        "AWS_DEFAULT_REGION": region,
        "AWS_MAX_ATTEMPTS": "1",
        "AWS_PAGER": "",
    }


class CloudWatchClient:
    """Host collection access, independent of agent tool authorization."""

    def __init__(
        self, access: CollectionAccess, runner: CliRunner = run_json
    ) -> None:
        self.access = access
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
                self.access.region,
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

    def _identity(self, identity: dict[str, Any], role: str) -> None:
        expected = (
            f"arn:aws:sts::{self.access.account_id}:"
            f"assumed-role/{role.rsplit('/', 1)[-1]}/"
        )
        arn = identity.get("Arn")

        if (
            identity.get("Account") != self.access.account_id
            or type(arn) is not str
            or not arn.startswith(expected)
        ):
            raise AwsCallError("unexpected_aws_identity")

    def _assume(self, deadline: Deadline) -> dict[str, str]:
        environment = host_environment(self.access.region)

        self._identity(
            self._call(
                "sts", "get-caller-identity", [], environment, deadline
            ),
            self.access.host_role_arn,
        )
        response = self._call(
            "sts",
            "assume-role",
            [
                "--role-arn",
                self.access.role_arn,
                "--role-session-name",
                "hermes-lab-collector",
                "--duration-seconds",
                "900",
            ],
            environment,
            deadline,
        )
        credentials = response.get("Credentials")

        if type(credentials) is not dict:
            raise AwsCallError("invalid_credentials")

        for name, variable in (
            ("AccessKeyId", "AWS_ACCESS_KEY_ID"),
            ("SecretAccessKey", "AWS_SECRET_ACCESS_KEY"),
            ("SessionToken", "AWS_SESSION_TOKEN"),
        ):
            value = credentials.get(name)

            if type(value) is not str or not value:
                raise AwsCallError("invalid_credentials")

            environment[variable] = value

        environment["AWS_EC2_METADATA_DISABLED"] = "true"

        self._identity(
            self._call(
                "sts", "get-caller-identity", [], environment, deadline
            ),
            self.access.role_arn,
        )

        return environment

    def filter_log_events(
        self,
        start: int,
        end: int,
        limit: int,
        token: str | None,
        deadline: Deadline,
    ) -> dict[str, Any]:
        if self.environment is None:
            self.environment = self._assume(deadline)

        arguments = [
            "--log-group-name",
            self.access.log_group,
            "--start-time",
            str(start),
            "--end-time",
            str(end),
            "--filter-pattern",
            "",
            "--limit",
            str(limit),
            "--no-unmask",
        ]

        if token is not None:
            arguments.extend(["--next-token", token])

        return self._call(
            "logs", "filter-log-events", arguments, self.environment, deadline
        )
