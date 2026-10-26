from . import client


def invoke(name: str, arguments: object) -> str:
    try:
        result = client.request(name, arguments)
        return client.encode(result).decode("ascii")
    except client.ClientError as error:
        return client.encode({"ok": False, "error": error.code}).decode("ascii")
    except Exception:
        return '{"ok":false,"error":"tool_failure"}'


def search_run_logs(args: object, **kwargs: object) -> str:
    return invoke("search_run_logs", args)


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


def search_log_events(args: object, **kwargs: object) -> str:
    return invoke("search_log_events", args)
