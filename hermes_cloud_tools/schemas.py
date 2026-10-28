RUN_ID = {
    "type": "string",
    "pattern": "^[a-f0-9]{24}$",
    "description": "The active incident's run ID supplied by the collector.",
}
MAX_BYTES = {
    "type": "integer",
    "minimum": 1,
    "maximum": 262144,
    "description": (
        "Maximum serialized evidence bytes; default 65536. "
        "Shared investigation budgets also apply."
    ),
}
