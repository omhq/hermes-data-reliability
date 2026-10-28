import logging
from datetime import datetime, timezone

from pipeline.logging_config import log_event as emit_event

LOGGER = logging.getLogger(__name__)


def log_event(
    event_name: str,
    message: str,
    *,
    level: int = logging.INFO,
    **details: object,
) -> None:
    emit_event(
        LOGGER,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pipeline": "synthetic-loader",
            "component": "processor",
            "event": event_name,
            "message": message,
            **details,
        },
        level=level,
    )
