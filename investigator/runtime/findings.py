import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from investigator.json_input import decode

if TYPE_CHECKING:
    from investigator.runtime.evidence import EvidenceVerifier


TEXT_FIELDS = (
    "summary",
    "failed_stage",
    "explanation",
    "uncertainty",
    "proposed_action",
)


@dataclass(frozen=True)
class Finding:
    incident_id: str
    text: dict[str, str]
    citations: list[dict[str, str]]

    @classmethod
    def from_value(cls, value: object, incident_id: str) -> Finding:
        """Validate report structure; references are opaque tool-issued IDs."""
        if type(value) is not dict or value.get("run_id") != incident_id:
            raise ValueError("finding run_id mismatch")
        if set(value) != {"run_id", "evidence", *TEXT_FIELDS}:
            raise ValueError("unexpected finding fields")

        text: dict[str, str] = {}

        for key in TEXT_FIELDS:
            field = value[key]

            if type(field) is not str or not 1 <= len(field) <= 8000:
                raise ValueError("missing or oversized finding field")

            text[key] = field

        evidence = value["evidence"]

        if type(evidence) is not list or not 1 <= len(evidence) <= 12:
            raise ValueError("finding needs evidence references")

        citations: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        for citation in evidence:
            if type(citation) is not dict or set(citation) != {
                "evidence_id",
                "reference",
            }:
                raise ValueError("citation needs an evidence ID and reference")

            evidence_id = citation["evidence_id"]
            reference = citation["reference"]

            if (
                type(evidence_id) is not str
                or not re.fullmatch(r"[a-f0-9]{32}", evidence_id)
                or type(reference) is not str
                or not 1 <= len(reference) <= 2048
            ):
                raise ValueError("invalid evidence citation")
            if (evidence_id, reference) in seen:
                raise ValueError("duplicate evidence citation")

            seen.add((evidence_id, reference))
            citations.append(
                {"evidence_id": evidence_id, "reference": reference}
            )

        return cls(incident_id, text, citations)


class FindingParser:
    def __init__(self, verifier: EvidenceVerifier) -> None:
        self.verifier = verifier

    def parse(self, stdout: str, incident_id: str) -> dict[str, Any]:
        results: list[dict[str, Any]] = []

        for line in stdout.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue

            if isinstance(item, dict) and item.get("type") == "result":
                results.append(item)

        if (
            len(results) != 1
            or results[0].get("exit_code") != 0
            or results[0].get("error")
            or type(results[0].get("text")) is not str
        ):
            raise ValueError("missing or failed Hermes result")

        result = results[0]
        answer = result["text"].strip()

        if answer.startswith("```json\n") and answer.endswith("```"):
            answer = answer[8:-3].strip()

        finding = Finding.from_value(
            decode(answer.encode("utf-8")), incident_id
        )
        verified = self.verifier.verify(incident_id, finding.citations)

        return {
            "run_id": finding.incident_id,
            **finding.text,
            "evidence": verified.receipts,
            "evidence_warnings": verified.limitations,
            "duration_ms": result.get("duration_ms"),
            "tokens": result.get("tokens"),
        }
