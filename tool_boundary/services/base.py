import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from tool_boundary.models import (
    IncidentContext,
    PolicyDenied,
    PolicyLimits,
    ResourceContext,
)
from tool_boundary.results import Evidence
from tool_boundary.validation import fields, require
from tool_boundary.wire import ExecutionError

if TYPE_CHECKING:
    from tool_boundary.contracts import ReadBackend, ToolExtension
    from tool_boundary.executor import Session
    from tool_boundary.models import ApprovedAction, ResolvedRead
    from tool_boundary.process import Deadline
    from tool_boundary.registry import ConfiguredTools


@dataclass(frozen=True)
class Scope:
    context: IncidentContext | ResourceContext
    limits: PolicyLimits
    mode: Literal["run", "log", "resources"]

    def incident(self) -> IncidentContext:
        if not isinstance(self.context, IncidentContext):
            raise PolicyDenied("invalid_context")

        return self.context

    def resources(self) -> ResourceContext:
        if not isinstance(self.context, ResourceContext):
            raise PolicyDenied("invalid_context")

        return self.context

    def describe(self) -> dict[str, Any]:
        context = self.context
        result = {
            "account_id": context.account_id,
            "region": context.region,
            "expires_at_ms": context.expires_at_ms,
            "max_calls": self.limits.max_calls,
            "total_bytes": self.limits.total_bytes,
        }

        if isinstance(context, ResourceContext):
            result.update(
                profile=context.profile,
                start_time_ms=context.start_time_ms,
                end_time_ms=context.end_time_ms,
            )

        return result


@dataclass(frozen=True)
class CliRequest:
    service: str
    operation: str
    arguments: tuple[str, ...]
    body: bool = False


class ToolProvider(ABC):
    package: ClassVar[str]
    toolset: ClassVar[str]
    service: ClassVar[str]
    modes: ClassVar[dict[str, frozenset[str]]]

    def __init__(
        self,
        configuration: dict[str, Any],
        extension: ToolExtension | None = None,
    ) -> None:
        self.configuration = configuration
        self.catalog: ConfiguredTools | None = None

    @classmethod
    def plugin_directory(cls) -> Path:
        return Path("investigator/tools") / cls.toolset

    @classmethod
    def tools(cls) -> frozenset[str]:
        return frozenset().union(*cls.modes.values())

    @classmethod
    def agent_options(cls, entry: object, enabled: bool) -> dict[str, Any]:
        if type(entry) is not dict or entry:
            raise ValueError("Unsupported tool package options")

        return {}

    @classmethod
    def host_options(
        cls, configuration: dict[str, Any], enabled: bool
    ) -> dict[str, Any]:
        return {}

    def _description(
        self, scope: Scope, arguments: dict[str, Any]
    ) -> ResolvedRead:
        fields(arguments, set(), {"max_bytes"})
        return "local", "DescribeScope", {}, None, None

    def resources(self) -> dict[str, Any]:
        return {}

    def describe(self, scope: Scope) -> dict[str, Any]:
        return {**scope.describe(), **self.resources()}

    @abstractmethod
    def validate_scope(self, scope: Scope) -> None: ...

    @abstractmethod
    def authorize(
        self,
        scope: Scope,
        tool: str,
        arguments: dict[str, Any],
        maximum: int,
        now: int,
    ) -> ResolvedRead: ...

    @abstractmethod
    def request(self, action: ApprovedAction) -> CliRequest: ...

    @abstractmethod
    def normalize(
        self,
        action: ApprovedAction,
        response: dict[str, Any],
        body: bytes | None,
    ) -> Evidence: ...

    def read(
        self,
        session: Session,
        action: ApprovedAction,
        backend: ReadBackend,
        deadline: Deadline,
        now: int,
    ) -> Evidence:
        if action.service == "local":
            return Evidence(self.describe(session.policy.scope()), [])

        if (
            not session.environment
            or time.monotonic() >= session.credentials_until
        ):
            session.environment = backend.authenticate(deadline)
            session.credentials_until = time.monotonic() + 840

        deadline.remaining()
        response, body = backend.execute(action, session.environment, deadline)

        if type(response) is not dict:
            raise ExecutionError("invalid_aws_response")

        return self.normalize(action, response, body)

    def check_action(self, action: ApprovedAction) -> None:
        require(action.service == self.service, "invalid_execution_plan")
        require(action.max_pages == 1, "invalid_execution_plan")

    def command(
        self,
        action: ApprovedAction,
        operation: str,
        flags: dict[str, str],
        *,
        json_fields: frozenset[str] = frozenset(),
        fixed: tuple[str, ...] = (),
        body: bool = False,
        cli_service: str | None = None,
        excluded: frozenset[str] = frozenset(),
    ) -> CliRequest:
        self.check_action(action)
        parameters = action.parameters_dict()
        require(
            set(parameters) == set(flags) | excluded, "invalid_execution_plan"
        )
        arguments = []

        for name, flag in flags.items():
            value = parameters[name]

            if name in json_fields:
                value = json.dumps(value, separators=(",", ":"))

            arguments.extend((flag, str(value)))

        return CliRequest(
            cli_service or self.service,
            operation,
            (*arguments, *fixed),
            body,
        )
