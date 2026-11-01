from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from .log_policy import LogPolicy, log_context
from .models import IncidentContext, ResourceContext
from .policy import RequestPolicy
from .resource_policy import ResourcePolicy, profile_context
from .services.cloudwatch import CloudWatchProvider
from .validation import fields, integer, require

if TYPE_CHECKING:
    from .contracts import ToolExtension
    from .registry import ConfiguredTools


@dataclass(frozen=True)
class ContextGrant:
    policy: RequestPolicy | ResourcePolicy
    context: IncidentContext | ResourceContext
    ttl: int


class ContextFactory(ABC):
    def __init__(
        self,
        configuration: dict[str, Any],
        providers: ConfiguredTools,
        clock: Callable[[], int],
        extension: ToolExtension | None,
    ) -> None:
        self.configuration = configuration
        self.providers = providers
        self.clock = clock
        self.extension = extension

    @abstractmethod
    def accepts(self, request: object) -> bool: ...

    @abstractmethod
    def open(self, request: object, owner_uid: int) -> ContextGrant: ...


class AwsContextFactory(ContextFactory):
    def source(self, request: object) -> tuple[dict[str, Any], object]:
        configuration = self.configuration

        if type(request) is dict and "expected_source" in request:
            expected = fields(
                request["expected_source"],
                {"account_id", "region", "log_group"},
                {"bucket"},
            )
            require(
                all(
                    expected[key] == configuration[key]
                    for key in expected
                    if key != "log_group"
                )
                and type(expected["log_group"]) is str
                and expected["log_group"]
                in CloudWatchProvider.collection_log_groups(configuration),
                "source_scope_changed",
            )
            configuration = {
                **configuration,
                "log_group": expected["log_group"],
            }
            request = {
                key: value
                for key, value in request.items()
                if key != "expected_source"
            }

        return configuration, request


class RunContextFactory(AwsContextFactory):
    def accepts(self, request: object) -> bool:
        return True

    def open(self, request: object, owner_uid: int) -> ContextGrant:
        configuration, request = self.source(request)
        request = fields(
            request,
            {"run_id", "attempt_ids", "incident_time_ms"},
            {"ttl_seconds"},
        )
        attempts = request["attempt_ids"]
        require(
            type(attempts) is list and len(attempts) <= 20, "invalid_context"
        )
        ttl = integer(request.get("ttl_seconds", 300), 1, 300, "invalid_ttl")
        context = IncidentContext(
            configuration["account_id"],
            configuration["region"],
            configuration["bucket"],
            configuration["log_group"],
            request["run_id"],
            tuple(attempts),
            request["incident_time_ms"],
            self.clock() + ttl * 1000,
        )
        policy = RequestPolicy(
            context, clock=self.clock, providers=self.providers
        )
        return ContextGrant(policy, context, ttl)


class LogContextFactory(AwsContextFactory):
    def accepts(self, request: object) -> bool:
        return type(request) is dict and "observation_id" in request

    def open(self, request: object, owner_uid: int) -> ContextGrant:
        configuration, request = self.source(request)
        context, ttl = log_context(configuration, request, self.clock())
        request = fields(
            request,
            {"observation_id", "incident_time_ms"},
            {"ttl_seconds"},
        )
        policy = LogPolicy(
            context,
            request["observation_id"],
            clock=self.clock,
            providers=self.providers,
        )
        return ContextGrant(policy, context, ttl)


class ResourceContextFactory(ContextFactory):
    def accepts(self, request: object) -> bool:
        return type(request) is dict and "profile" in request

    def open(self, request: object, owner_uid: int) -> ContextGrant:
        require(owner_uid == 0, "resource_scope_forbidden")
        context, ttl = profile_context(
            self.configuration, request, self.clock()
        )
        extension = self.extension

        if extension is None:
            policy = ResourcePolicy(
                context, clock=self.clock, providers=self.providers
            )
        else:
            policy = extension.resource_policy(
                context, self.clock, self.providers
            )

        return ContextGrant(policy, context, ttl)


class CapabilityContextFactory(AwsContextFactory):
    def accepts(self, request: object) -> bool:
        return self.extension is not None and self.extension.accepts_context(
            request
        )

    def open(self, request: object, owner_uid: int) -> ContextGrant:
        extension = self.extension

        if extension is None:
            raise ValueError("Capability is not installed")

        _, request = self.source(request)
        policy, context, ttl = extension.context(request, self.clock)
        policy.bind(self.providers)
        return ContextGrant(policy, context, ttl)
