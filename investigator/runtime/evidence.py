import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from investigator.runtime.sessions import InvestigationSession


@dataclass(frozen=True)
class VerifiedEvidence:
    receipts: list[dict[str, Any]]
    limitations: list[dict[str, Any]]


class EvidenceVerifier(Protocol):
    def verify(
        self, incident_id: str, citations: list[dict[str, str]]
    ) -> VerifiedEvidence: ...


class SessionEvidenceVerifier:
    def __init__(self, session: InvestigationSession) -> None:
        self.session = session

    def verify(
        self, incident_id: str, citations: list[dict[str, str]]
    ) -> VerifiedEvidence:
        """Resolve exact citations through the session's trusted executor."""
        verified = self.session.evidence(citations)
        receipts = verified["receipts"]
        limitations = verified["limitations"]

        if len(receipts) != len(citations):
            raise ValueError("incomplete evidence verification")

        for citation, receipt in zip(citations, receipts):
            if type(receipt) is not dict:
                raise ValueError("evidence provenance mismatch")

            digest = receipt.get("sha256")

            if (
                any(
                    receipt.get(key) != value
                    for key, value in citation.items()
                )
                or receipt.get("session_id") != self.session.session_id
                or receipt.get("run_id") != incident_id
                or type(digest) is not str
                or not re.fullmatch(r"[a-f0-9]{64}", digest)
                or type(receipt.get("truncated")) is not bool
                or type(receipt.get("source")) is not dict
                or type(receipt.get("observed_at_ms")) is not int
                or type(receipt.get("data_bytes")) is not int
            ):
                raise ValueError("evidence provenance mismatch")

        if any(type(item) is not dict for item in limitations):
            raise ValueError("invalid evidence limitations")

        return VerifiedEvidence(receipts, limitations)
