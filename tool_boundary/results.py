from dataclasses import dataclass
from typing import Any

from .models import ApprovedAction
from .wire import ExecutionError, encode


def integer_parameter(action: ApprovedAction, name: str, minimum: int) -> int:
    """Read a response bound from the trusted plan without coercing it."""
    value = action.parameters.get(name)

    if type(value) is not int or value < minimum:
        raise ExecutionError("invalid_execution_plan")

    return value


def fit_items(
    name: str, items: list[dict[str, Any]], maximum: int
) -> tuple[dict[str, list[dict[str, Any]]], bool]:
    data = {name: []}

    if len(encode(data)) > maximum:
        raise ExecutionError("response_budget_too_small")

    truncated = False
    for item in items:
        candidate = {name: data[name] + [item]}

        if len(encode(candidate)) > maximum:
            truncated = True
            break

        data = candidate

    return data, truncated


def selected_fields(
    response: dict[str, Any], expected: dict[str, type[object]]
) -> dict[str, Any]:
    selected = {}

    for name, kind in expected.items():
        if name not in response:
            continue
        if type(response[name]) is not kind:
            raise ExecutionError("invalid_aws_response")

        selected[name] = response[name]

    return selected


@dataclass(frozen=True)
class Evidence:
    data: dict[str, Any]
    references: list[dict[str, str]]
    truncated: bool = False
    collection: str | None = None

    def bounded(self) -> Evidence:
        references = []

        for reference in self.references:
            if (
                len(references) >= 20
                or len(encode(references + [reference])) > 4096
            ):
                break

            references.append(reference)

        limited = len(references) != len(self.references)
        data = self.data

        if limited:
            if self.collection is None:
                raise ExecutionError("reference_budget_exceeded")

            data = {
                **data,
                self.collection: data[self.collection][: len(references)],
            }

        return Evidence(
            data, references, self.truncated or limited, self.collection
        )
