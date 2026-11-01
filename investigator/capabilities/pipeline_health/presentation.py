from typing import Any

from .validation import utc_text


def describe_observation(result: dict[str, Any]) -> dict[str, Any]:
    """Expose recorded times directly without model timestamp arithmetic."""
    return {
        **result,
        "timing": {
            "scheduled_at_utc": utc_text(result["scheduled_at"]),
            "deadline_utc": utc_text(result["deadline"]),
            "observed_at_utc": utc_text(result["observed_at"]),
        },
    }
