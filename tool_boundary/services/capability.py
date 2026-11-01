from typing import TYPE_CHECKING, Any

from tool_boundary.results import Evidence
from tool_boundary.wire import ExecutionError

from .base import CliRequest, Scope, ToolProvider

if TYPE_CHECKING:
    from tool_boundary.contracts import ReadBackend, ToolExtension
    from tool_boundary.executor import Session
    from tool_boundary.models import ApprovedAction, ResolvedRead
    from tool_boundary.process import Deadline


class CapabilityProvider(ToolProvider):
    def __init__(
        self,
        configuration: dict[str, Any],
        extension: ToolExtension | None = None,
    ) -> None:
        super().__init__(configuration, extension)
        self.extension = extension

    def validate_scope(self, scope: Scope) -> None:
        extension = self.extension

        if extension is not None:
            if extension.service != self.service:
                raise ExecutionError("invalid_context")

            extension.validate_scope(scope)

    def authorize(
        self,
        scope: Scope,
        tool: str,
        arguments: dict[str, Any],
        maximum: int,
        now: int,
    ) -> ResolvedRead:
        raise ExecutionError("capability_scope_required")

    def request(self, action: ApprovedAction) -> CliRequest:
        raise ExecutionError("invalid_execution_plan")

    def normalize(
        self,
        action: ApprovedAction,
        response: dict[str, Any],
        body: bytes | None,
    ) -> Evidence:
        raise ExecutionError("invalid_execution_plan")

    def read(
        self,
        session: Session,
        action: ApprovedAction,
        backend: ReadBackend,
        deadline: Deadline,
        now: int,
    ) -> Evidence:
        extension = self.extension

        if (
            extension is None
            or extension.service != self.service
            or action.service != self.service
        ):
            raise ExecutionError("capability_unavailable")

        data = extension.execute(session, action, backend, deadline, now)
        return Evidence(data, extension.references(data))
