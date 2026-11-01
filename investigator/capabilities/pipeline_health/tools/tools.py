from .client import invoke


def list_pipeline_expectations(args: object, **kwargs: object) -> str:
    return invoke("list_pipeline_expectations", args)


def check_pipeline_expectations(args: object, **kwargs: object) -> str:
    return invoke("check_pipeline_expectations", args)


def get_pipeline_check_history(args: object, **kwargs: object) -> str:
    return invoke("get_pipeline_check_history", args)
