from .client import invoke


def describe_sqs_scope(args: object, **kwargs: object) -> str:
    return invoke("describe_sqs_scope", args)


def get_sqs_queue_attributes(args: object, **kwargs: object) -> str:
    return invoke("get_sqs_queue_attributes", args)
