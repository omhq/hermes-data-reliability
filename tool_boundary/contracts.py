from typing import Any, Protocol

from .models import ApprovedAction
from .process import Deadline


class AuditSink(Protocol):
    def write(self, record: dict[str, Any]) -> None: ...


class ReadBackend(Protocol):
    """Trusted adapter; credentials and raw responses stay on the host."""

    def assume(self, deadline: Deadline) -> dict[str, str]: ...

    def execute(
        self,
        action: ApprovedAction,
        environment: dict[str, str],
        deadline: Deadline,
    ) -> tuple[dict[str, Any], bytes | None]: ...
