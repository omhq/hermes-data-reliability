from typing import TYPE_CHECKING, Any, Callable, Protocol

from .models import ApprovedAction
from .process import Deadline

if TYPE_CHECKING:
    from .executor import Executor, Session
    from .models import ResourceContext
    from .registry import ConfiguredTools
    from .resource_policy import ResourcePolicy
    from .services.base import Scope


class AuditSink(Protocol):
    def write(self, record: dict[str, Any]) -> None: ...


class ReadBackend(Protocol):
    """Trusted adapter; credentials and raw responses stay on the host."""

    def authenticate(self, deadline: Deadline) -> dict[str, str]: ...

    def execute(
        self,
        action: ApprovedAction,
        environment: dict[str, str],
        deadline: Deadline,
    ) -> tuple[dict[str, Any], bytes | None]: ...


class ToolExtension(Protocol):
    """Installed host code; model input cannot select an extension."""

    service: str

    def validate_scope(self, scope: Scope) -> None: ...

    def accepts_context(self, request: object) -> bool: ...

    def context(
        self, request: object, clock: Callable[[], int]
    ) -> tuple[ResourcePolicy, ResourceContext, int]: ...

    def resource_policy(
        self,
        context: ResourceContext,
        clock: Callable[[], int],
        providers: ConfiguredTools,
    ) -> ResourcePolicy: ...

    def execute(
        self,
        session: Session,
        action: ApprovedAction,
        backend: ReadBackend,
        deadline: Deadline,
        now: int,
    ) -> dict[str, Any]: ...

    def references(self, data: dict[str, Any]) -> list[dict[str, str]]: ...

    def control(
        self, executor: Executor, request: dict[str, Any]
    ) -> dict[str, Any] | None: ...
