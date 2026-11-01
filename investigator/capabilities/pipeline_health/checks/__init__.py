from collections.abc import Callable
from dataclasses import dataclass

from ..observations import Observation, Status
from .events import evaluate as event_observed
from .presence import evaluate


@dataclass(frozen=True)
class Check:
    adapter: str
    evaluate: Callable[[Observation], tuple[Status, str]]


CHECKS = {
    "object_exists": Check("s3_object", evaluate),
    "event_observed": Check("collected_events", event_observed),
}
