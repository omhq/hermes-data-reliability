from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from tool_boundary.models import ApprovedAction

type Status = Literal["pending", "pass", "fail", "unknown"]
type ReadEvidence = Callable[[ApprovedAction], dict[str, Any]]


@dataclass(frozen=True)
class Target:
    adapter: str
    resource: str
    scope: str
    location: Mapping[str, str]

    def describe(self) -> dict[str, object]:
        return {
            "adapter": self.adapter,
            "resource": self.resource,
            "scope": self.scope,
            **self.location,
        }


@dataclass(frozen=True)
class Observation:
    facts: Mapping[str, bool | int | str | None]
    coverage: Literal["complete", "partial", "unavailable"]
    reason: str
    limitation: str


class EvidenceAdapter(Protocol):
    name: str
    supporting_tools: frozenset[str]

    def source_options(self, value: object) -> Mapping[str, object]: ...

    def resource_profile(
        self,
        value: object,
        config: dict[str, Any],
    ) -> Mapping[str, object]: ...

    def resolve(
        self,
        resource: str,
        profile: Mapping[str, object],
        options: Mapping[str, object],
        delivery_id: str,
        account: str,
    ) -> Target: ...

    def observe(
        self,
        target: Target,
        action: ApprovedAction,
        read: ReadEvidence,
        *,
        start: int,
        end: int,
        now: int,
    ) -> Observation: ...

    def constrain_read(
        self, target: Target, action: ApprovedAction
    ) -> ApprovedAction: ...

    def permits_read(
        self,
        target: Target,
        tool: str,
        arguments: dict[str, Any],
    ) -> bool: ...
