from ..observations import Observation, Status
from .presence import evaluate as presence


def evaluate(observation: Observation) -> tuple[Status, str]:
    if observation.facts.get("waiting_for_collection") is True:
        return "pending", observation.reason

    return presence(observation)
