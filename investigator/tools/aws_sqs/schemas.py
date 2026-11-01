MAX_BYTES = {"type": "integer", "minimum": 1, "maximum": 262144}


DESCRIBE_SQS_SCOPE = {
    "name": "describe_sqs_scope",
    "description": (
        "Show approved queue ARNs and session limits without AWS discovery."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": [],
        "properties": {"max_bytes": MAX_BYTES},
    },
}


GET_SQS_QUEUE_ATTRIBUTES = {
    "name": "get_sqs_queue_attributes",
    "description": (
        "Read approved SQS queue settings and approximate current counts. "
        "Does not receive messages. Counts do not prove delivery of a "
        "particular message."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["queue_arn"],
        "properties": {
            "max_bytes": MAX_BYTES,
            "queue_arn": {
                "type": "string",
                "maxLength": 256,
                "description": "An exact ARN from the approved scope.",
            },
        },
    },
}
