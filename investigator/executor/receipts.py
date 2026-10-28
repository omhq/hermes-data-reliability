from typing import TYPE_CHECKING, Any

from .models import PolicyDenied
from .policy import fields, require
from .wire import encode

if TYPE_CHECKING:
    from investigator.executor.executor import Session
    from investigator.executor.models import ApprovedAction


def references_for(
    action: ApprovedAction, data: dict[str, Any], truncated: bool
) -> tuple[list[dict[str, str]], bool]:
    """Absence requires an exact, complete listing, never a failed read."""
    parameters = action.parameters
    references = []

    if action.operation == "GetObject":
        references.append(
            {
                "reference": f"s3://{parameters['Bucket']}/{parameters['Key']}",
                "observation": "object_sample",
            }
        )
    elif action.operation == "ListObjectsV2":
        for item in data["objects"]:
            references.append(
                {
                    "reference": f"s3://{parameters['Bucket']}/{item['key']}",
                    "observation": "object_listed",
                }
            )

        if action.exact_key and not references and not truncated:
            references.append(
                {
                    "reference": (f"s3://{parameters['Bucket']}/{action.exact_key}"),
                    "observation": "object_absent",
                }
            )
    elif action.operation == "FilterLogEvents":
        references = [
            {
                "reference": "cloudwatch-event:" + item["event_id"],
                "observation": "log_event",
            }
            for item in data["events"]
        ]
    elif action.operation == "LookupEvents":
        references = [
            {
                "reference": (
                    f"cloudtrail-event:{action.account_id}:{action.region}:"
                    + item["event_id"]
                ),
                "observation": "cloudtrail_event",
            }
            for item in data["events"]
        ]

    bounded = []

    for reference in references:
        if len(bounded) >= 20 or len(encode(bounded + [reference])) > 4096:
            break

        bounded.append(reference)

    return bounded, len(bounded) != len(references)


def select_receipts(session: Session, citations: object) -> dict[str, Any]:
    """Resolve model citations against this session's host-owned receipts."""
    if type(citations) is not list or not 1 <= len(citations) <= 12:
        raise PolicyDenied("invalid_citations")

    selected = []
    seen = set()

    for citation in citations:
        citation = fields(citation, {"evidence_id", "reference"}, set())
        evidence_id = citation["evidence_id"]
        reference = citation["reference"]

        require(
            type(evidence_id) is str and type(reference) is str,
            "invalid_citations",
        )
        require((evidence_id, reference) not in seen, "duplicate_citation")
        seen.add((evidence_id, reference))

        receipt = session.receipts.get(evidence_id)

        if receipt is None:
            raise PolicyDenied("unknown_evidence")

        matching = [
            item for item in receipt["references"] if item["reference"] == reference
        ]

        require(len(matching) == 1, "evidence_reference_mismatch")
        selected.append(
            {
                **{key: value for key, value in receipt.items() if key != "references"},
                **matching[0],
            }
        )

    limitations = list(session.failures)

    if len(session.failures) == 20:
        limitations.append({"code": "failure_details_limit_reached"})

    limitations += [
        {"evidence_id": receipt["evidence_id"], "code": "partial_evidence"}
        for receipt in session.receipts.values()
        if receipt["truncated"]
    ]

    return {"receipts": selected, "limitations": limitations}
