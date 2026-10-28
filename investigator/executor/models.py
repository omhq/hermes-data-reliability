from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

type ResolvedRead = tuple[str, str, dict[str, Any], str | None, str | None]


class PolicyDenied(PermissionError):
    """Return a stable denial code without echoing untrusted request data."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class IncidentContext:
    """Supplied by the trusted host, never by model arguments."""

    account_id: str
    region: str
    bucket: str
    log_group: str
    run_id: str
    attempt_ids: tuple[str, ...]
    incident_time_ms: int
    expires_at_ms: int


@dataclass(frozen=True)
class PolicyLimits:
    """Host-owned caps; callers may request smaller responses only."""

    max_calls: int = 10
    default_bytes: int = 64 * 1024
    max_bytes: int = 256 * 1024
    total_bytes: int = 1024 * 1024
    max_events: int = 200
    max_keys: int = 20
    log_window_seconds: int = 900
    timeout_seconds: int = 30


@dataclass(frozen=True)
class ResourceContext:
    """Host-approved resources and time range for a whole conversation."""

    profile: str
    account_id: str
    region: str
    bucket: str
    log_group: str
    prefixes: tuple[str, ...]
    start_time_ms: int
    end_time_ms: int
    expires_at_ms: int

    @property
    def run_id(self) -> str:
        return ""


@dataclass(frozen=True)
class ApprovedAction:
    """An execution plan, not permission to run arbitrary AWS arguments.

    The executor enforces response limits and filters listed keys
    against exact_key or key_prefix before returning evidence to the model.
    """

    tool: str
    run_id: str
    account_id: str
    region: str
    service: str
    operation: str
    parameters: Mapping[str, object]
    max_bytes: int
    timeout_seconds: float
    expires_at_ms: int
    exact_key: str | None = None
    key_prefix: str | None = None
    max_pages: int = 1

    def __post_init__(self) -> None:
        # Freeze a copy so mutation of a caller's dictionary cannot change it!
        object.__setattr__(
            self, "parameters", MappingProxyType(dict(self.parameters))
        )
