import copy
from typing import TYPE_CHECKING, Any

from .models import PolicyDenied
from .validation import fields, require

if TYPE_CHECKING:
    from .executor import Session


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
            item
            for item in receipt["references"]
            if item["reference"] == reference
        ]

        require(len(matching) == 1, "evidence_reference_mismatch")
        selected.append(
            {
                **{
                    key: value
                    for key, value in receipt.items()
                    if key != "references"
                },
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

    return copy.deepcopy({"receipts": selected, "limitations": limitations})
