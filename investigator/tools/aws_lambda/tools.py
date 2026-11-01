from .client import invoke


def describe_lambda_scope(args: object, **kwargs: object) -> str:
    return invoke("describe_lambda_scope", args)


def get_lambda_function_configuration(args: object, **kwargs: object) -> str:
    return invoke("get_lambda_function_configuration", args)


def get_lambda_function_concurrency(args: object, **kwargs: object) -> str:
    return invoke("get_lambda_function_concurrency", args)


def get_lambda_event_source_mapping(args: object, **kwargs: object) -> str:
    return invoke("get_lambda_event_source_mapping", args)
