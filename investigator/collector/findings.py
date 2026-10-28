import re
import json

from typing import TYPE_CHECKING, Any

from investigator.executor.wire import decode

if TYPE_CHECKING:
    from investigator.runtime.sessions import InvestigationSession


TEXT_FIELDS = (
    "summary",
    "failed_stage",
    "explanation",
    "uncertainty",
    "proposed_action",
)


def validate_finding(
    value: object, run_id: str, bucket: str, logs_only: bool = False
) -> dict[str, Any]:
    if type(value) is not dict or value.get("run_id") != run_id:
        raise ValueError("finding run_id mismatch")
    if set(value) != {"run_id", "evidence", *TEXT_FIELDS}:
        raise ValueError("unexpected finding fields")

    for key in TEXT_FIELDS:
        if type(value.get(key)) is not str or not 1 <= len(value[key]) <= 8000:
            raise ValueError("missing or oversized finding field")

    citations = value["evidence"]

    if type(citations) is not list or not 1 <= len(citations) <= 12:
        raise ValueError("finding needs evidence references")

    seen = set()

    for citation in citations:
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

        if re.fullmatch(
            r"cloudtrail-event:[0-9]{12}:[a-z]{2}(?:-[a-z]+)+-[0-9]+:"
            r"[a-fA-F0-9-]{36}",
            reference,
        ):
            # The host resolves this reference against the session receipt;
            # a model-supplied event ID alone is never trusted evidence.
            continue

        if logs_only:
            if not reference.startswith("cloudwatch-event:"):
                raise ValueError("evidence outside log scope")

            continue

        allowed = (
            f"s3://{bucket}/input/{run_id}.json",
            f"s3://{bucket}/output/{run_id}.json",
        )

        if not (
            reference in allowed
            or re.fullmatch(
                r"s3://"
                + re.escape(bucket)
                + r"/runs/"
                + run_id
                + r"/[a-zA-Z0-9-]+\.json",
                reference,
            )
            or reference.startswith("cloudwatch-event:")
        ):
            raise ValueError("evidence outside incident scope")

    return value


def bind_receipts(
    finding: dict[str, Any], session: InvestigationSession
) -> dict[str, Any]:
    verified = session.evidence(finding["evidence"])
    receipts = verified["receipts"]

    if len(receipts) != len(finding["evidence"]):
        raise ValueError("incomplete evidence verification")

    for citation, receipt in zip(finding["evidence"], receipts):
        if (
            any(receipt.get(key) != value for key, value in citation.items())
            or receipt.get("session_id") != session.session_id
            or receipt.get("run_id") != finding["run_id"]
            or not re.fullmatch(r"[a-f0-9]{64}", receipt.get("sha256", ""))
            or type(receipt.get("truncated")) is not bool
            or type(receipt.get("source")) is not dict
            or type(receipt.get("observed_at_ms")) is not int
            or type(receipt.get("data_bytes")) is not int
        ):
            raise ValueError("evidence provenance mismatch")

    return {
        **finding,
        "evidence": receipts,
        "evidence_warnings": verified["limitations"],
    }


def parse_result(
    stdout: str,
    run_id: str,
    bucket: str,
    session: InvestigationSession,
    logs_only: bool = False,
) -> dict[str, Any]:
    results = []

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

    answer = results[0]["text"].strip()

    if answer.startswith("```json\n") and answer.endswith("```"):
        answer = answer[8:-3].strip()

    finding = validate_finding(
        decode(answer.encode("utf-8")), run_id, bucket, logs_only=logs_only
    )
    finding = bind_receipts(finding, session)
    finding["duration_ms"] = results[0].get("duration_ms")
    finding["tokens"] = results[0].get("tokens")

    return finding
