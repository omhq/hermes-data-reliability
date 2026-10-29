import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

type ResolvedRead = tuple[str, str, dict[str, Any], str | None, str | None]


def freeze_parameters(
    parameters: Mapping[str, object],
) -> Mapping[str, object]:
    if any(type(key) is not str for key in parameters):
        raise ValueError("invalid_execution_plan")

    return MappingProxyType(
        {key: freeze_value(value) for key, value in parameters.items()}
    )


def freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return freeze_parameters(value)
    if type(value) is list or type(value) is tuple:
        return tuple(freeze_value(item) for item in value)
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value

    raise ValueError("invalid_execution_plan")


def thaw_value(value: object) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw_value(item) for key, item in value.items()}
    if type(value) is tuple:
        return [thaw_value(item) for item in value]

    return value


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
        object.__setattr__(
            self, "parameters", freeze_parameters(self.parameters)
        )

    def parameters_dict(self) -> dict[str, Any]:
        """Return a detached provider payload from the immutable plan."""
        return {
            key: thaw_value(value) for key, value in self.parameters.items()
        }
