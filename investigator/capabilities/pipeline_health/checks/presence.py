from ..observations import Observation, Status


def evaluate(observation: Observation) -> tuple[Status, str]:
    exists = observation.facts.get("exists")

    if exists is True:
        return "pass", "evidence_present_at_observation"
    if exists is False and observation.coverage == "complete":
        return "fail", "evidence_absent_at_observation_after_deadline"

    return "unknown", observation.reason
