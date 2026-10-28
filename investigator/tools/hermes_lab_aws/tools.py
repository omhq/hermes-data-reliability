from hermes_cloud_tools.client import invoke


def list_run_objects(args: object, **kwargs: object) -> str:
    return invoke("list_run_objects", args)


def read_run_object(args: object, **kwargs: object) -> str:
    return invoke("read_run_object", args)


def list_aws_resources(args: object, **kwargs: object) -> str:
    return invoke("list_aws_resources", args)


def list_s3_objects(args: object, **kwargs: object) -> str:
    return invoke("list_s3_objects", args)


def read_s3_object(args: object, **kwargs: object) -> str:
    return invoke("read_s3_object", args)
