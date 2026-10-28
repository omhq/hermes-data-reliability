import json
import logging
import sys
from collections.abc import Mapping
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Preserve event fields at the top level for existing collector rules."""

    def format(self, record: logging.LogRecord) -> str:
        payload = (
            dict(record.msg)
            if isinstance(record.msg, dict)
            else {"message": record.getMessage()}
        )

        payload.setdefault(
            "timestamp",
            datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
        )

        payload["level"] = record.levelname
        payload["logger"] = record.name
        return json.dumps(payload, sort_keys=True)


def configure_logging() -> None:
    """Install one handler per package without duplicating Lambda root logs."""
    for namespace in ("pipeline", "producer", "processor"):
        logger = logging.getLogger(namespace)
        managed = [
            handler
            for handler in logger.handlers
            if isinstance(handler, logging.StreamHandler)
            and isinstance(handler.formatter, JsonFormatter)
        ]

        if managed:
            for handler in managed:
                handler.setStream(sys.stdout)
        else:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(JsonFormatter())
            logger.addHandler(handler)

        logger.setLevel(logging.INFO)
        logger.propagate = False


def log_event(
    logger: logging.Logger,
    fields: Mapping[str, object],
    *,
    level: int = logging.INFO,
) -> None:
    """Log reviewed fields without provider exception bodies."""
    logger.log(level, dict(fields))
