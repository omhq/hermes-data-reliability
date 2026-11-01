MAX_BYTES = {"type": "integer", "minimum": 1, "maximum": 262144}


DESCRIBE_LAMBDA_SCOPE = {
    "name": "describe_lambda_scope",
    "description": (
        "Show approved function ARNs and event-source mappings without AWS "
        "discovery."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": [],
        "properties": {"max_bytes": MAX_BYTES},
    },
}


GET_LAMBDA_FUNCTION_CONFIGURATION = {
    "name": "get_lambda_function_configuration",
    "description": (
        "Read selected operational configuration of an approved function. "
        "Environment values and code are withheld. Current configuration "
        "does not establish historical state."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["function_arn"],
        "properties": {
            "max_bytes": MAX_BYTES,
            "function_arn": {
                "type": "string",
                "maxLength": 256,
                "description": "An exact ARN from the approved scope.",
            },
        },
    },
}


GET_LAMBDA_FUNCTION_CONCURRENCY = {
    "name": "get_lambda_function_concurrency",
    "description": (
        "Read current reserved concurrency for an approved function. A "
        "missing reservation does not imply unlimited capacity. Does not "
        "provide invocation history."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["function_arn"],
        "properties": {
            "max_bytes": MAX_BYTES,
            "function_arn": {
                "type": "string",
                "maxLength": 256,
                "description": "An exact ARN from the approved scope.",
            },
        },
    },
}


GET_LAMBDA_EVENT_SOURCE_MAPPING = {
    "name": "get_lambda_event_source_mapping",
    "description": (
        "Read one approved SQS-to-Lambda mapping. Current settings do not "
        "prove that a particular message was delivered or processed."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["mapping_id"],
        "properties": {
            "max_bytes": MAX_BYTES,
            "mapping_id": {
                "type": "string",
                "pattern": "^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$",
            },
        },
    },
}
