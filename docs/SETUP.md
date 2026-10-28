# Setup

Run workstation commands from the repository root. Requirements: Terraform
1.5.7+, AWS CLI, Python 3.14, a deployment profile, and an SSH key pair.

## Provision AWS

The roots have independent inputs, providers, and state:

| Root | Owns |
| --- | --- |
| `infra/aws-agent` | EC2, its network, host/executor IAM, report email, health, and backups |
| `infra/aws-pipeline` | S3, SQS, producer/processor Lambdas, logs, and Scheduler |

Neither root reads the other's configuration, outputs, or state. You can deploy
and remove either independently. The agent takes explicit existing evidence
locations; those resources can be provisioned by any system. Its current executor
supports one bucket's `input/`, `output/`, and `runs/` prefixes and one log group.

Confirm each root's deployment account with its explicitly configured profile:

```bash
aws sso login --profile YOUR_PROFILE
aws sts get-caller-identity --profile YOUR_PROFILE --query Account --output text
```

For a new checkout, copy each root's `terraform.tfvars.example` to its ignored
`terraform.tfvars`. Configure `aws_profile`, `expected_account_id`, `aws_region`,
and `project_name` independently.

The optional synthetic pipeline requires S3, SQS, IAM, Lambda, Scheduler,
CloudWatch, and `iam:PassRole`. Keep generation disabled until smoke tests pass:

```bash
terraform -chdir=infra/aws-pipeline init
terraform -chdir=infra/aws-pipeline fmt -check
terraform -chdir=infra/aws-pipeline validate
terraform -chdir=infra/aws-pipeline plan -out=pipeline.tfplan
terraform -chdir=infra/aws-pipeline show pipeline.tfplan
```

Review the saved plan before applying:

```bash
terraform -chdir=infra/aws-pipeline apply pipeline.tfplan
```

Inspect this pipeline's public identifiers with
`terraform -chdir=infra/aws-pipeline output -json pipeline`.
To observe it, explicitly enter its bucket and log group in the agent's inputs;
there is no automatic connection between the roots.

Configure the agent's inputs:

| Input | Value |
| --- | --- |
| `ssh_key_name` | Existing EC2 key pair; alternatively use `ssh_public_key_path` |
| `ssh_cidr` | Trusted public IPv4 `/32`; wider than `/24` is rejected |
| `ami_id` | Reviewed Amazon Linux 2023 x86_64 image |
| `evidence_bucket_name` | Existing authorized evidence bucket |
| `evidence_log_group` | Existing authorized CloudWatch log group |
| `alert_email` | Optional recipient for agent reports |

Agent provisioning requires EC2/VPC, IAM, CloudWatch, SNS, Backup, KMS, and
`iam:PassRole`. Keys and SSO tokens stay outside Terraform; gateway/dashboard
ports stay closed.

```bash
terraform -chdir=infra/aws-agent init
terraform -chdir=infra/aws-agent fmt -check
terraform -chdir=infra/aws-agent validate
terraform -chdir=infra/aws-agent plan -out=agent.tfplan
terraform -chdir=infra/aws-agent show agent.tfplan
```

Review account, region, SSH source, evidence access, and replacements. Pin any
discovered AMI and regenerate the plan before applying:

```bash
terraform -chdir=infra/aws-agent apply agent.tfplan
```

## Deploy the runtime

Follow [verified SSH setup](OPERATIONS.md#connect-from-your-workstation).
On EC2, check bootstrap:

```bash
sudo cloud-init status --wait
test -f /srv/hermes-lab/bootstrap-complete
docker version
```

Bootstrap installs Python 3.14. On an existing EC2 host, install it first:

```bash
sudo dnf install -y --releasever=latest python3.14 python3.14-pip
/usr/bin/python3.14 --version
```

Leave the OS's `/usr/bin/python3` unchanged; project services use
`/usr/bin/python3.14`. Bootstrap edits can replace EC2 via
`user_data_replace_on_change`; review plans and preserve state. To update an
existing host, use the package install above and source deployment below.

On the workstation, adjust the key path as needed:

```bash
python3.14 -m investigator.runtime.deploy --ssh-key ~/.ssh/experiment.pem \
  --known-hosts .local/known_hosts --activate
```

Deployment and recovery read only `infra/aws-agent` state. Deployment uses its
saved SSH destination; review a refresh-only plan if stale. Without `--activate`,
it only stages source. Activation pulls the pinned
image (15-minute limit), verifies Python (30-second probe), builds tools,
checkpoints state, and installs the executor. It preserves pause choice,
authentication, and findings. Unsupported incident database formats stop
activation before services stop, without conversion/reset.

Activation prepares `/srv/hermes-lab/collector-venv-py314` with pinned PyYAML
and validates patterns in a bounded worker. First use installs
`investigator/runtime/observations.yaml`; later activations preserve/validate
your YAML and its required investigation defaults. Lambda also uses Python
3.14; image changes verify the interpreter before services stop.
Collection uses `/etc/hermes-lab/executor.json` as its independent allowlist.

On a fresh EC2 host, authenticate Hermes:

```bash
image=$(cat /srv/hermes-lab/code/investigator/runtime/image.txt)
docker run -it --rm -v /srv/hermes-lab/hermes:/opt/data \
  "$image" auth add openai-codex --no-browser
sudo labctl start
sudo labctl status
```

Approve the device login; if prompted, enable device-code sign-in in ChatGPT
Security Settings and request a new code. Later logins use `sudo labctl login`.
Keep OAuth files on EC2; do not copy another installation's credentials.

## Enable generation

Invoke the producer with unique `scheduled_at` values for `scenario: "success"`
and `scenario: "malformed"`. Check uploads, SQS, logs, and output/attempt objects
via [Operations](OPERATIONS.md). The processor expects SQS, not Scheduler payloads.

Set `pipeline_schedule_enabled = true` in `infra/aws-pipeline/terraform.tfvars`
and review/apply a pipeline plan. Agent health, report delivery, and backups
are configured and checked separately.

## Access verification

After smoke fixtures exist, run IAM policy checks from the workstation:

```bash
mkdir -p .local
terraform -chdir=infra/aws-agent output -json investigator > .local/investigator.json
python3.14 -m investigator.runtime.verify_investigator_access policy \
  --profile YOUR_PROFILE --config .local/investigator.json
```

Write/delete checks use IAM simulation, which may differ from live behavior.
After full deployment, run live checks on EC2:

```bash
cd /srv/hermes-lab/code
python3.14 -m investigator.runtime.verify_investigator_access live \
  --config /srv/hermes-lab/investigator.json
```

Live checks use the host role, sample at most 1 KiB/prefix, test allowed/denied
reads, and never print credentials.

Full activation includes the executor. For a separate update or isolated plugin
check, run on the workstation:

```bash
python3.14 -m investigator.runtime.deploy_executor \
  --ssh-key ~/.ssh/experiment.pem --known-hosts .local/known_hosts
python3.14 -m investigator.runtime.deploy_plugin \
  --ssh-key ~/.ssh/experiment.pem --known-hosts .local/known_hosts
```

Executor releases live under `/opt/hermes-lab-executor/`; plugin checks stage
under `/opt/hermes-lab-plugins/releases/`. Native discovery/dispatch uses a
short-lived context in a network-isolated container, without provider credentials
or model calls. Cleanup revokes access; both commands preserve runtime/pause state.

On EC2, verify the executor:

```bash
sudo systemctl is-active hermes-lab-executor.service
sudo sh -c 'cd /opt/hermes-lab-executor/code && python3.14 -m investigator.executor.verify'
```

Verification checks reads, denials, bounds, budgets, receipts, and revocation.
`--restart-check` also restarts the executor to test old-token denial.

