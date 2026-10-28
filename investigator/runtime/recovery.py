import argparse
import json
import subprocess
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from investigator.cli_output import write_json

if TYPE_CHECKING:
    from investigator.runtime.iam_policy_checks import AwsRequest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def aws(
    profile: str,
    config: dict[str, Any],
    service: str,
    action: str,
    *flags: str,
) -> dict[str, Any]:
    response = subprocess.check_output(
        [
            "aws",
            service,
            action,
            "--profile",
            profile,
            "--region",
            config["region"],
            "--output",
            "json",
            "--no-cli-pager",
            *flags,
        ]
    )

    return json.loads(response)


def restore_volume(
    request: AwsRequest, config: dict[str, Any], recovery_point: str
) -> dict[str, Any]:
    """Verify vault membership before restoring to a separate volume."""
    account = (
        config.get("account_id") or config["backup_role_arn"].split(":")[4]
    )
    point = request(
        "backup",
        "describe-recovery-point",
        "--backup-vault-name",
        config["backup_vault"],
        "--recovery-point-arn",
        recovery_point,
    )
    volume_prefix = f"arn:aws:ec2:{config['region']}:{account}:volume/"

    if point["ResourceType"] != "EBS" or not point["ResourceArn"].startswith(
        volume_prefix
    ):
        raise SystemExit(
            "Recovery point is not an EBS backup in this lab's vault/account"
        )

    metadata = request(
        "backup",
        "get-recovery-point-restore-metadata",
        "--backup-vault-name",
        config["backup_vault"],
        "--recovery-point-arn",
        recovery_point,
    )["RestoreMetadata"]

    metadata.update(
        availabilityZone=config["availability_zone"],
        volumeType="gp3",
        encrypted="true",
        iops="3000",
        throughput="125",
    )

    return request(
        "backup",
        "start-restore-job",
        "--recovery-point-arn",
        recovery_point,
        "--iam-role-arn",
        config["restore_role_arn"],
        "--resource-type",
        "EBS",
        "--metadata",
        json.dumps(metadata),
    )


def perform_action(
    args: argparse.Namespace, config: dict[str, Any]
) -> dict[str, Any] | list[dict[str, Any]]:
    request = partial(aws, args.profile, config)
    account = (
        config.get("account_id") or config["backup_role_arn"].split(":")[4]
    )

    if request("sts", "get-caller-identity")["Account"] != account:
        raise SystemExit("AWS profile resolves to the wrong account")

    if args.action == "backup":
        volume_arn = (
            f"arn:aws:ec2:{config['region']}:{account}:"
            f"volume/{config['root_volume_id']}"
        )

        return request(
            "backup",
            "start-backup-job",
            "--backup-vault-name",
            config["backup_vault"],
            "--resource-arn",
            volume_arn,
            "--iam-role-arn",
            config["backup_role_arn"],
            "--lifecycle",
            "DeleteAfterDays=7",
        )

    if args.action == "list":
        response = request(
            "backup",
            "list-recovery-points-by-backup-vault",
            "--backup-vault-name",
            config["backup_vault"],
        )
        fields = ("RecoveryPointArn", "CreationDate", "Status", "ResourceType")

        return [
            {key: point.get(key) for key in fields}
            for point in response["RecoveryPoints"]
        ]

    if args.action == "backup-status":
        response = request(
            "backup", "describe-backup-job", "--backup-job-id", args.job_id
        )
        fields = ("State", "StatusMessage", "PercentDone", "RecoveryPointArn")

        return {key: response.get(key) for key in fields}

    if args.action == "restore-volume":
        return restore_volume(request, config, args.recovery_point)

    response = request(
        "backup", "describe-restore-job", "--restore-job-id", args.job_id
    )
    fields = ("Status", "StatusMessage", "CreatedResourceArn", "PercentDone")

    return {key: response.get(key) for key in fields}


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "action",
        choices=[
            "backup",
            "list",
            "backup-status",
            "restore-volume",
            "restore-status",
        ],
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--job-id")
    parser.add_argument("--recovery-point")

    args = parser.parse_args()

    if args.action in ("backup-status", "restore-status") and not args.job_id:
        parser.error("--job-id is required")
    if args.action == "restore-volume" and not args.recovery_point:
        parser.error("--recovery-point is required")

    config = json.loads(
        subprocess.check_output(
            ["terraform", "output", "-json", "operations"],
            cwd=PROJECT_ROOT / "infra/aws-agent",
        )
    )

    write_json(perform_action(args, config), indent=2)


if __name__ == "__main__":
    main()
