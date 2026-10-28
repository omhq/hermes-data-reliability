from hermes_cloud_tools.client import invoke


def describe_cloudwatch_scope(args: object, **kwargs: object) -> str:
    return invoke("describe_cloudwatch_scope", args)


def search_run_logs(args: object, **kwargs: object) -> str:
    return invoke("search_run_logs", args)


def search_log_events(args: object, **kwargs: object) -> str:
    return invoke("search_log_events", args)
