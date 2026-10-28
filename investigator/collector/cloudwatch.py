import time
from typing import TYPE_CHECKING, Any

from investigator.executor.models import ApprovedAction
from investigator.executor.process import Deadline
from investigator.executor.wire import ExecutionError, encode

if TYPE_CHECKING:
    from investigator.executor.audit import AuditLog
    from investigator.executor.aws import AwsBackend


MAX_PAGES = 5
MAX_WINDOW_MS = 2 * 60 * 60 * 1000
PAGE_EVENTS = 100


class CloudWatchReader:
    def __init__(self, backend: AwsBackend, audit: AuditLog) -> None:
        self.backend = backend
        self.account_id = backend.config["account_id"]
        self.audit = audit
        self.environment = None

        now = int(time.time() * 1000)
        self.deadline = Deadline(
            60, now + 60000, lambda: int(time.time() * 1000)
        )

    def read(
        self,
        source: dict[str, Any],
        start: int,
        end: int,
        token: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        approved = self.backend.config

        if any(
            source[key] != approved[key] for key in ("region", "log_group")
        ):
            raise ExecutionError("resource_not_allowed")

        parameters = {
            "logGroupName": source["log_group"],
            "startTime": start,
            "endTime": end,
            "filterPattern": "",
            "limit": PAGE_EVENTS,
            "unmask": False,
        }

        if token is not None:
            parameters["nextToken"] = token

        action = ApprovedAction(
            tool="collect_log_events",
            run_id="",
            account_id=approved["account_id"],
            region=approved["region"],
            service="logs",
            operation="FilterLogEvents",
            parameters=parameters,
            max_bytes=2 * 1024 * 1024,
            timeout_seconds=30,
            expires_at_ms=self.deadline.expires_at_ms,
        )

        self.audit.write(
            {
                "status": "collection_read",
                "source": {
                    "account_id": approved["account_id"],
                    "region": approved["region"],
                    "log_group": source["log_group"],
                    "start_ms": start,
                    "end_ms": end,
                },
            }
        )

        try:
            if self.environment is None:
                self.environment = self.backend.assume(self.deadline)

            response, _ = self.backend.execute(
                action, self.environment, self.deadline
            )
            events, continuation = validate_page(response, start, end)
        except Exception:
            self.audit.write({"status": "collection_failed"})
            raise

        self.audit.write(
            {
                "status": "collection_completed",
                "events": len(events),
                "partial": continuation is not None,
            }
        )

        return events, continuation


def validate_page(
    response: dict[str, Any], start: int, end: int
) -> tuple[list[dict[str, Any]], str | None]:
    if type(response) is not dict or type(response.get("events")) is not list:
        raise ExecutionError("invalid_aws_response")

    events = response["events"]

    if len(events) > PAGE_EVENTS or len(encode(response)) > 2 * 1024 * 1024:
        raise ExecutionError("upstream_response_too_large")

    for event in events:
        if (
            type(event) is not dict
            or type(event.get("eventId")) is not str
            or not 1 <= len(event["eventId"]) <= 256
            or type(event.get("timestamp")) is not int
            or not start <= event["timestamp"] <= end
            or type(event.get("message")) is not str
            or type(event.get("logStreamName")) is not str
        ):
            raise ExecutionError("invalid_aws_response")

    token = response.get("nextToken")

    if token is not None and (
        type(token) is not str or not 1 <= len(token) <= 4096
    ):
        raise ExecutionError("invalid_aws_response")

    return events, token
