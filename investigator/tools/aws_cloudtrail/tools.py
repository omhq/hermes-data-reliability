from hermes_cloud_tools.client import invoke


def describe_cloudtrail_scope(args: object, **kwargs: object) -> str:
    return invoke("describe_cloudtrail_scope", args)


def lookup_cloudtrail_events(args: object, **kwargs: object) -> str:
    return invoke("lookup_cloudtrail_events", args)
