# Setup

You'll need Terraform 1.5.7+, AWS CLI, Python 3.14, a deployment profile, and an
SSH key pair. Run commands on your local machine from the repository root.

## Provision AWS

The Terraform roots are independent: each has its own inputs, provider, and state.

| Root | Owns |
| --- | --- |
| `infra/aws-agent` | EC2, network, IAM, report email, health, backups |
| `infra/aws-pipeline` | S3, SQS, Lambdas, logs, Scheduler |

Neither reads the other's configuration or state. You can deploy/remove either
separately. Agent evidence locations may come from any system; current access
supports one bucket's `input/`, `output/`, and `runs/` prefixes and one log group.

Confirm each deployment account with its explicit profile:

```bash
aws sso login --profile YOUR_PROFILE
aws sts get-caller-identity --profile YOUR_PROFILE --query Account --output text
```

For a new checkout, copy each `terraform.tfvars.example` to ignored
`terraform.tfvars`. Set `aws_profile`, `expected_account_id`, `aws_region`,
and `project_name` independently.

The optional synthetic pipeline needs S3, SQS, IAM, Lambda, Scheduler, CloudWatch,
and `iam:PassRole`. Keep generation disabled until smoke tests pass:

```bash
terraform -chdir=infra/aws-pipeline init
terraform -chdir=infra/aws-pipeline fmt -check
terraform -chdir=infra/aws-pipeline validate
terraform -chdir=infra/aws-pipeline plan -out=pipeline.tfplan
terraform -chdir=infra/aws-pipeline show pipeline.tfplan
```

Review the saved plan, then apply:

```bash
terraform -chdir=infra/aws-pipeline apply pipeline.tfplan
```

Inspect identifiers with `terraform -chdir=infra/aws-pipeline output -json pipeline`.
Enter its bucket/log group explicitly in the agent inputs; the roots do not
connect automatically.

Configure the agent:

| Input | Value |
| --- | --- |
| `ssh_key_name` | Existing EC2 key pair, or use `ssh_public_key_path` |
| `ssh_cidr` | Trusted public IPv4 `/32`; wider than `/24` is rejected |
| `ami_id` | Reviewed Amazon Linux 2023 x86_64 image |
| `evidence_bucket_name` | Authorized existing bucket |
| `evidence_log_group` | Authorized existing log group |
| `sqs_queue_arns` | Optional exact queue ARNs for `hermes-sqs` |
| `lambda_function_arns` | Optional unqualified function ARNs for `hermes-lambda` |
| `lambda_event_source_mappings` | Optional UUID-to-function/queue bindings for mapping reads |
| `alert_email` | Optional report recipient |

For the optional SQS and Lambda packages, copy queue/function ARNs and mapping
UUIDs from the pipeline Terraform output into your ignored agent
`terraform.tfvars`. The output includes `queue_arn`, `dead_letter_arn`,
`function_arn`, `producer_function_arn`, and `event_source_mapping_id`.
Approve only the resources you want investigated. A mapping entry has this shape
(replace every placeholder with the corresponding output):

```hcl
lambda_event_source_mappings = {
  "MAPPING_UUID" = {
    function_arn = "APPROVED_FUNCTION_ARN"
    queue_arn    = "APPROVED_QUEUE_ARN"
  }
}
```

Enable `hermes-sqs` and/or `hermes-lambda` in the agent YAML, then review the
agent Terraform plan and deploy through the existing workflow. The plan grants
only the selected metadata reads. Each enabled package requires its explicit
resource allowlist; an empty mapping allowlist disables mapping reads.

Agent provisioning needs EC2/VPC, IAM, CloudWatch, SNS, Backup, KMS, and
`iam:PassRole`. Keep keys/SSO tokens outside Terraform. Gateway/dashboard
ports stay closed.

```bash
terraform -chdir=infra/aws-agent init
terraform -chdir=infra/aws-agent fmt -check
terraform -chdir=infra/aws-agent validate
terraform -chdir=infra/aws-agent plan -out=agent.tfplan
terraform -chdir=infra/aws-agent show agent.tfplan
```

Review account, region, SSH source, access, and replacements. Pin any discovered
AMI and regenerate the plan before applying:

```bash
terraform -chdir=infra/aws-agent apply agent.tfplan
```

## Deploy the runtime

The pipeline-health capability is enabled in the agent tool configuration.
Full deployment installs its host implementation, Hermes adapter, investigation
skill, and scheduled runner. Its initial YAML has no expectations; configure
the delivery schedule and output convention using the
[operations guide](OPERATIONS.md#configure-delivery-expectations).

Installation accepts only the current database and configuration formats.
For breaking changes, discard the affected lab data and recreate it with the
current release. See [fresh lab state](RECOVERY.md#breaking-format-changes).

Choose [tool packages](ACCESS.md#tool-packages) in
`investigator/runtime/config.yaml` before planning the agent root. Deployment
rejects selections that differ from saved Terraform outputs before uploading.

Follow [verified SSH setup](OPERATIONS.md#connect-from-your-local-machine).
On the runtime host, check bootstrap:

```bash
sudo cloud-init status --wait
test -f /srv/hermes-lab/bootstrap-complete
docker version
```

Bootstrap installs Python 3.14. On an existing host, install it first:

```bash
sudo dnf install -y --releasever=latest python3.14 python3.14-pip
/usr/bin/python3.14 --version
```

Leave `/usr/bin/python3` alone; project services use `/usr/bin/python3.14`.
Bootstrap edits can replace EC2 via `user_data_replace_on_change`; review plans
and preserve state. Existing hosts can use the install above and deployment below.

On your local machine, adjust the key path:

```bash
python3.14 -m investigator.runtime.deploy --ssh-key ~/.ssh/experiment.pem \
  --known-hosts .local/known_hosts --activate
```

Deployment/recovery read only agent Terraform state. Deployment uses its saved
SSH destination; review a refresh-only plan if stale. Without `--activate`,
source is staged only. Activation:

- Pulls the pinned image (15-minute limit) and verifies Python (30-second probe).
- Builds tools, checkpoints state, and installs the executor and separate timers.
- Generates a read-only package manifest for model-side YAML toolset selection.
- Preserves pause choice, authentication, and findings.
- Rejects unsupported incident and notification database formats before
  services stop. Recreate affected lab state before activation.

The release includes `observations`, `collector`, `incidents`, `investigator` (including its
capabilities), and `tool_boundary`.
Investigation uses host Python 3.14 and the standard library.
Collection runs `python -m collector.poll` in
`/srv/hermes-lab/collector-venv-py314`, with pinned PyYAML. Separate log-rule consumption runs in the same environment.
Its independent allowlist is `/etc/hermes-lab/executor.json`.

First installation supplies the source catalog, log-rule consumer, and empty
pipeline-health consumer templates from `investigator/runtime/`. The compiler
validates their subscriptions and installs a shared collection plan. Later
activations preserve your YAML. The current formats are catalog version 2,
log-rule version 1, and pipeline-health version 4.

The collector owns `/var/lib/hermes-lab-observations/observations.sqlite3`.
The directory is owned by `ec2-user`, grouped to `hermes-lab-executor`, with
permissions `0750`; the database uses `0640`. The executor can query evidence
without writing it. Consumers publish incidents to `findings/incidents.sqlite3`.
An independent timer runs each built-in consumer. No pipeline code change or
new dependency is required. Add approved CloudWatch groups through the optional
`collection_log_groups` Terraform input before declaring those catalog sources;
review the IAM plan before applying it. CloudTrail collection requires the
existing enabled CloudTrail package and explicit resource names.
Lambda uses Python 3.14 too; image changes verify Python before services stop.

On a fresh runtime host, log in to Hermes:

```bash
image=$(cat /srv/hermes-lab/code/investigator/runtime/image.txt)
docker run -it --rm -v /srv/hermes-lab/hermes:/opt/data \
  "$image" auth add openai-codex --no-browser
sudo labctl start
sudo labctl status
```

Approve the device login. If prompted, enable device-code sign-in in ChatGPT
Security Settings, then request a new code. Later logins use `sudo labctl login`.
Keep OAuth files on the runtime host; never borrow another installation's
credentials.

## Enable generation

Time for a smoke test. Invoke the producer with unique `scheduled_at` values
for `scenario: "success"` and `scenario: "malformed"`. Check uploads, SQS, logs,
and output/attempt objects using [Operations](OPERATIONS.md). The processor
expects SQS messages, not Scheduler payloads.

Then set `pipeline_schedule_enabled = true` in the pipeline `terraform.tfvars`
and review/apply a plan. Check agent health, email, and backups separately.

## Access verification

After smoke fixtures exist, run IAM checks from your local machine:

```bash
mkdir -p .local
terraform -chdir=infra/aws-agent output -json investigator > .local/investigator.json
python3.14 -m investigator.runtime.verify_investigator_access policy \
  --profile YOUR_PROFILE --config .local/investigator.json
```

Write/delete checks use IAM simulation, which can differ from live behavior.
After deployment, run live checks on the runtime host:

```bash
cd /srv/hermes-lab/code
python3.14 -m investigator.runtime.verify_investigator_access live \
  --config /srv/hermes-lab/investigator.json
```

These use the host role, sample at most 1 KiB/prefix, test allowed/denied reads,
and never print credentials.

Full activation includes the executor. For a separate update or isolated plugin
check, run on your local machine:

```bash
python3.14 -m investigator.runtime.deploy_executor \
  --ssh-key ~/.ssh/experiment.pem --known-hosts .local/known_hosts
python3.14 -m investigator.runtime.deploy_plugin \
  --ssh-key ~/.ssh/experiment.pem --known-hosts .local/known_hosts
```

Executor releases go under `/opt/hermes-lab-executor/`; plugin checks stage under
`/opt/hermes-lab-plugins/releases/`. Discovery/dispatch uses a short-lived context
in a network-isolated container, with no provider credentials or model calls.
Cleanup revokes access. Both commands preserve runtime/pause state.

On the runtime host, verify the executor:

```bash
sudo systemctl is-active hermes-lab-executor.service
sudo sh -c 'cd /opt/hermes-lab-executor/code && python3.14 -m investigator.runtime.verify_executor'
```

Checks cover reads, denials, bounds, budgets, receipts, and revocation.
`--restart-check` also restarts it to verify old-token denial.
