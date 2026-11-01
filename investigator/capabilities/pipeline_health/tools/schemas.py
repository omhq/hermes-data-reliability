MAX_BYTES = {"type": "integer", "minimum": 1024, "maximum": 262144}
PIPELINE = {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,47}$"}
OCCURRENCE = {
    "type": "string",
    "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
}

LIST_PIPELINE_EXPECTATIONS = {
    "name": "list_pipeline_expectations",
    "description": (
        "List this session's configured pipeline expectations and delivery "
        "schedules and objectives. Supply pipeline_id for one pipeline's full "
        "investigation instructions. Configuration is not health evidence."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": [],
        "properties": {"max_bytes": MAX_BYTES, "pipeline_id": PIPELINE},
    },
}
CHECK_PIPELINE_EXPECTATIONS = {
    "name": "check_pipeline_expectations",
    "description": (
        "Check one authorized pipeline delivery. Returns pending, pass, fail, "
        "or unknown with evidence. Presence does not establish valid content "
        "or timely arrival. Failed or incomplete reads cannot prove absence."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["pipeline_id", "expectation_id", "occurrence"],
        "properties": {
            "pipeline_id": PIPELINE,
            "expectation_id": PIPELINE,
            "occurrence": OCCURRENCE,
            "max_bytes": MAX_BYTES,
        },
    },
}
GET_PIPELINE_CHECK_HISTORY = {
    "name": "get_pipeline_check_history",
    "description": (
        "Read up to twenty saved observations of one authorized delivery, "
        "including original observation times and any later presence after "
        "an observed failure. Missing history is unknown. Does not call AWS."
    ),
    "parameters": CHECK_PIPELINE_EXPECTATIONS["parameters"],
}
