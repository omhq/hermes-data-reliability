import json

from typing import Any, Iterator, Protocol

from hermes_cloud_tools.packages import ToolPackages


class AwsRequest(Protocol):
    """A scoped AWS command callable whose response is decoded JSON."""

    def __call__(
        self, service: str, operation: str, /, *arguments: str
    ) -> dict[str, Any]: ...


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def policy_cases(
    config: dict[str, Any],
) -> Iterator[tuple[str, str, str | None, bool]]:
    bucket = f"arn:aws:s3:::{config['bucket']}"
    logs = config["log_group_arn"] + ":*"
    cloudtrail = (
        "hermes-cloudtrail" in ToolPackages.from_executor_config(config).enabled
    )

    yield "cloudtrail:LookupEvents", "*", None, cloudtrail

    for action in (
        "cloudtrail:StartLogging",
        "cloudtrail:StopLogging",
        "cloudtrail:DeleteTrail",
        "cloudtrail:PutEventSelectors",
    ):
        yield action, "*", None, False

    for prefix in ("input/", "output/", "runs/"):
        yield "s3:ListBucket", bucket, prefix, True
        yield "s3:GetObject", bucket + "/" + prefix + "probe.json", None, True

    for prefix in ("", "private/", "input-other/"):
        yield "s3:ListBucket", bucket, prefix, False

    yield "s3:GetObject", bucket + "/private/probe.json", None, False
    yield "s3:GetObject", bucket + "-outside/input/probe.json", None, False
    yield "logs:FilterLogEvents", logs, None, True
    yield (
        "logs:FilterLogEvents",
        logs.replace(config["log_group"], config["log_group"] + "-outside"),
        None,
        False,
    )

    for action in ("s3:PutObject", "s3:DeleteObject", "s3:PutObjectAcl"):
        yield action, bucket + "/input/probe.json", None, False

    for action in ("s3:PutBucketPolicy", "s3:DeleteBucket"):
        yield action, bucket, None, False

    for action in ("logs:PutLogEvents", "logs:DeleteLogGroup", "logs:Unmask"):
        yield action, logs, None, False

    for action in ("iam:PassRole", "iam:PutRolePolicy", "sts:AssumeRole"):
        yield action, config["role_arn"], None, False

    for action in (
        "s3:ListAllMyBuckets",
        "cloudwatch:PutMetricData",
        "backup:ListRecoveryPointsByBackupVault",
    ):
        yield action, "*", None, False


def verify_role(request: AwsRequest, config: dict[str, Any]) -> dict[str, Any]:
    name = config["role_name"]
    role = request("iam", "get-role", "--role-name", name)["Role"]
    trust = role["AssumeRolePolicyDocument"]["Statement"]

    require(len(trust) == 1, "Unexpected trust statements")
    require(trust[0]["Effect"] == "Allow", "Unexpected trust effect")
    require(trust[0]["Action"] == "sts:AssumeRole", "Unexpected trust action")
    require(
        trust[0]["Principal"] == {"AWS": config["host_role_arn"]},
        "Trust must name only the lab host role",
    )
    require(role["MaxSessionDuration"] == 3600, "Unexpected session duration")

    attached = request("iam", "list-attached-role-policies", "--role-name", name)

    require(not attached["AttachedPolicies"], "Unexpected managed policies")

    inline = request("iam", "list-role-policies", "--role-name", name)

    require(
        inline["PolicyNames"] == [config["read_policy_name"]],
        "Unexpected inline policies",
    )

    policy = request(
        "iam",
        "get-role-policy",
        "--role-name",
        name,
        "--policy-name",
        config["read_policy_name"],
    )["PolicyDocument"]

    statements = policy["Statement"]

    cloudtrail = (
        "hermes-cloudtrail" in ToolPackages.from_executor_config(config).enabled
    )

    require(
        len(statements) == 3 + int(cloudtrail),
        "Unexpected evidence policy statements",
    )
    require(
        all(statement["Effect"] == "Allow" for statement in statements),
        "Unexpected evidence policy effect",
    )
    require(
        {statement["Action"] for statement in statements}
        == {"s3:ListBucket", "s3:GetObject", "logs:FilterLogEvents"}
        | ({"cloudtrail:LookupEvents"} if cloudtrail else set()),
        "Unexpected investigator actions",
    )

    return {"check": "role_trust_and_policy_inventory", "passed": True}


def verify_policy(request: AwsRequest, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Check read and write permissions without executing mutations."""
    results = [verify_role(request, config)]

    for action, resource, prefix, allowed in policy_cases(config):
        flags = [
            "--policy-source-arn",
            config["role_arn"],
            "--action-names",
            action,
            "--resource-arns",
            resource,
        ]

        # IAM can report this key on unmatched statements for other actions.
        # Keep simulation context complete; only ListBucket uses the prefix.
        flags += [
            "--context-entries",
            json.dumps(
                [
                    {
                        "ContextKeyName": "s3:prefix",
                        "ContextKeyValues": [
                            prefix if prefix is not None else "input/"
                        ],
                        "ContextKeyType": "string",
                    },
                    {
                        "ContextKeyName": "aws:RequestedRegion",
                        "ContextKeyValues": [config["region"]],
                        "ContextKeyType": "string",
                    },
                ]
            ),
        ]

        response = request("iam", "simulate-principal-policy", *flags)
        evaluations = response["EvaluationResults"]

        require(len(evaluations) == 1, "Unexpected simulation result count")

        decision = evaluations[0]["EvalDecision"]
        expected = {"allowed"} if allowed else {"implicitDeny", "explicitDeny"}

        require(
            decision in expected,
            f"Unexpected IAM decision for {action}: {decision}",
        )
        require(
            not evaluations[0].get("MissingContextValues"),
            "Simulation is missing policy context",
        )
        results.append(
            {
                "check": "iam_simulation",
                "action": action,
                "prefix": prefix,
                "decision": decision,
                "passed": True,
            }
        )

    return results
