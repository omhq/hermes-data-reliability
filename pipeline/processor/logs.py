import json

from datetime import datetime, timezone


def log_event(event_name: str, message: str, **details: object) -> None:
    print(
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "pipeline": "synthetic-loader",
                "component": "processor",
                "event": event_name,
                "message": message,
                **details,
            },
            sort_keys=True,
        ),
        flush=True,
    )
