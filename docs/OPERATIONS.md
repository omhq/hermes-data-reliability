# Operating Hermes Lab

Daily controls for the deployed investigator.

## Connect from your workstation

Run workstation commands from the repository root:

```bash
cd /path/to/hermes-data-reliability
```

Substitute your profile, region, and key below; the profile must match
`infra/terraform.tfvars`. Keep keys, variables, and state out of Git.

If AWS reports an expired SSO session:

```bash
aws sso login --profile YOUR_PROFILE
```

Get the instance ID from Terraform and its live IP from AWS; stop/start can
change the address:

```bash
lab_instance_id=$(terraform -chdir=infra output -raw instance_id)
lab_ip=$(aws ec2 describe-instances \
  --profile YOUR_PROFILE --region us-east-1 \
  --instance-ids "$lab_instance_id" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
printf 'SSH destination: ec2-user@%s\n' "$lab_ip"
```

If the IP is empty or `None`, check instance state. Otherwise connect:

```bash
ssh -i ~/.ssh/experiment.pem \
  -o IdentitiesOnly=yes \
  -o UserKnownHostsFile=.local/known_hosts \
  -o StrictHostKeyChecking=yes \
  "ec2-user@$lab_ip"
```

For port-22 timeouts, compare your current public IPv4 with `ssh_cidr` and the
live security group. Update the trusted `/32` if needed; review the plan first.

For first/replacement connections, verify the host fingerprint against the EC2
console system log. Create `.local/` and use `StrictHostKeyChecking=ask` for that
verified connection, then `yes`. Never bypass checking or blindly remove warnings.

Terraform also exposes cached addresses:

```bash
terraform -chdir=infra output -raw public_ip
terraform -chdir=infra output -raw ssh_destination
```

These reflect the last refresh. Prefer live lookup after stop/start; use current
Terraform state after replacement, which also changes the instance ID.

## Stop, start, and check the agent on EC2

Use the host's control command:

| Action                                                 | Command               |
| ------------------------------------------------------ | --------------------- |
| Check runtime, timers, and health                      | `sudo labctl status`  |
| Pause automatic investigation and stop both containers | `sudo labctl pause`   |
| Resume the agent and collector                         | `sudo labctl start`   |
| Restart the agent and collector                        | `sudo labctl restart` |
| Chat with AWS investigation tools                      | `sudo labctl chat`    |

`stop` aliases `pause`. Use `labctl` to coordinate containers/collector; choices,
results, and login survive reboots. `status` includes hourly budget `used`,
`remaining`, and `next_available_utc`.

Chat shares access rules with automation. Detection continues during
chat; automatic claims wait without spending attempts.

Pause leaves EC2 billed, health/backups active, and synthetic generation running
until separately disabled. Intentional pause is healthy.

## Investigate through chat

On EC2, start the lab before `sudo labctl chat`. For example:

> Find failed loads in the last two hours, inspect their S3 input and attempt
> logs, and explain the failures with evidence.

Optional launches, also on EC2:

```bash
sudo labctl chat --minutes 15 --lookback-hours 6
sudo labctl chat --query "Show my approved AWS resources."
```

Follow-ups share budget. `/exit`, disconnect, or expiry ends access; history
needs a newly authorized launch. `--profile lab` is the host access profile,
not an AWS login. Direct SSH needs `ssh -t ... 'sudo labctl chat'`.

## Pause or resume synthetic data generation

On your workstation, edit the ignored `infra/terraform.tfvars`:

```hcl
pipeline_schedule_enabled = false # true to resume
```

Review and apply the saved plan:

```bash
terraform -chdir=infra plan -out=lab.tfplan
terraform -chdir=infra show lab.tfplan
terraform -chdir=infra apply lab.tfplan
```

A schedule-only plan should change only Scheduler state; resolve other changes
first. The example defaults to `false`; local inputs set the deployment value.
Check live state in AWS, since Terraform outputs may be stale.

## Read logs and results on EC2

Follow producer uploads from your **workstation**:

```bash
aws logs tail /aws/lambda/hermes-lab-producer \
  --profile YOUR_PROFILE --region us-east-1 \
  --since 10m --format short --follow
```

Follow processor/SQS events from your **workstation** (`hermes-lab-loader`):

```bash
aws logs tail /aws/lambda/hermes-lab-loader \
  --profile YOUR_PROFILE --region us-east-1 \
  --since 10m --format short --follow
```

Ctrl-C stops following. Correlate UTC JSON logs by `run_id`, plus processor
SQS/attempt IDs. Trace upload, receipt, loading, validation, writes, and completion.
The final per-file `status` drives collector detection.

From the **workstation repository root**, inspect deployment identifiers:

```bash
terraform -chdir=infra output -json pipeline
```

Use `queue_url` or `dead_letter_url` to inspect without consuming messages:

```bash
aws sqs get-queue-attributes --queue-url QUEUE_URL \
  --profile YOUR_PROFILE --region us-east-1 \
  --attribute-names ApproximateNumberOfMessages \
    ApproximateNumberOfMessagesNotVisible ApproximateNumberOfMessagesDelayed
```

Counts are approximate; five receives send failures to the DLQ. Disabling the
schedule stops new input, not queued processing. `labctl pause` stops neither.

On **EC2**, inspect collector and health logs:

Host stderr logs appear in the journal with UTC time, level, and module.
CloudWatch pipeline logs are JSON with `level`/`logger`.

```bash
sudo journalctl -u hermes-lab-poller.service -n 50 --no-pager
sudo journalctl -u hermes-lab-health.service -n 20 --no-pager
sudo systemctl list-timers 'hermes-lab-*' --all --no-pager
```

Add `-f` to follow journal entries; Ctrl-C exits. View findings on EC2:

```bash
ls -lt /srv/hermes-lab/findings/reports/
tail -n 5 /srv/hermes-lab/findings/incidents.jsonl
```

| Path on EC2                                  | Contents                                          |
| -------------------------------------------- | ------------------------------------------------- |
| `/srv/hermes-lab/findings/incidents.sqlite3` | Saved incidents, collection progress, and attempt history |
| `/srv/hermes-lab/findings/incidents.jsonl`   | Exported incidents and findings                   |
| `/srv/hermes-lab/findings/reports/`          | Readable completed reports                        |
| `/srv/hermes-lab/evidence/`                  | Collected evidence snapshots                      |

The poller ticks every 15 seconds after work finishes. Due sources use their
YAML interval; backlog/errors retry next tick. Hermes runs only for eligible
incidents within three attempts/rolling hour; excess incidents wait. A running
container need not be making model calls. Emails cover health, not findings.

## Configure the collector

On **EC2**, from `/srv/hermes-lab/code`, supply a definition:

```bash
sudo labctl start \
  --observations /srv/hermes-lab/code/investigator/runtime/observations.yaml
sudo labctl status
```

The runtime validates against host policy, atomically installs at
`/srv/hermes-lab/observations.yaml`, then starts services. Use an absolute path;
`restart` also accepts `--observations`. Rejection preserves services/pause
choice. Later starts/reboots reuse the installed copy. Region/group must match
the allowlist; only initial default installation resolves deployment locations.

`investigator/runtime/start.sh` forwards to `labctl start`; the poller passes
the installed YAML to the host collector, outside the model container.

Edit the installed YAML directly for subsequent changes:

```bash
sudoedit /srv/hermes-lab/observations.yaml
/srv/hermes-lab/collector-venv-py314/bin/python -m investigator.collector.poll \
  --config /etc/hermes-lab/executor.json \
  --observations /srv/hermes-lab/observations.yaml --validate-only
```

Validation checks structure, location, and compilation without AWS/model calls.
Valid edits load next invocation without timer changes. Shared instructions use
`investigation.instructions`, with per-source overrides.

Replay one text block on EC2 below, or use the workstation venv with
`requirements.txt` and local paths:

```bash
/srv/hermes-lab/collector-venv-py314/bin/python -m investigator.collector.preview \
  --observations /srv/hermes-lab/observations.yaml \
  --source processor_logs --text /tmp/example-log-block.txt
```

Preview returns rules, severities, and character spans without AWS/model calls.
Each input file is one block.

`/srv/hermes-lab/findings/last-poll.json` reports source status, cursor, next poll,
sanitized error, pages/events/bytes, and lag. SQLite retains originals/revisions;
`collection-audit.jsonl` in the same directory holds the host audit. Rule edits
may replay history without reopening completed incidents or resetting attempts.
Keep all findings/state private.

## Restart collection from a new point

For a fresh experiment, run **on EC2**:

```bash
sudo labctl reset-collector --since now
```

Reset archives findings, reports, and queue under
`/srv/hermes-lab/checkpoints/collector-reset-*/` and reports the path. The fresh
queue uses installed YAML; attempts/budgets, authentication, and pause choice
remain intact. A running lab resumes.

To install an edited YAML and read the last fifteen minutes instead:

```bash
sudo labctl reset-collector --since 15m \
  --observations /srv/hermes-lab/code/investigator/runtime/observations.yaml
```

`--since` defaults to `now`, or accepts `s`/`m`/`h` up to `24h`. Its boundary
survives restarts and limits overlap. YAML validates before services stop.
Finish active investigations or pause first. Do not delete lock files; they
coordinate processes, not reading positions. Ordinary edits need no reset.

## Stop or start the EC2 host

Disable generation to avoid new backlog, then `sudo labctl pause` on EC2.
On the workstation, in the shell holding `$lab_instance_id`:

```bash
aws ec2 stop-instances --profile YOUR_PROFILE --region us-east-1 \
  --instance-ids "$lab_instance_id"
aws ec2 wait instance-stopped --profile YOUR_PROFILE --region us-east-1 \
  --instance-ids "$lab_instance_id"
```

Start it again:

```bash
aws ec2 start-instances --profile YOUR_PROFILE --region us-east-1 \
  --instance-ids "$lab_instance_id"
aws ec2 wait instance-status-ok --profile YOUR_PROFILE --region us-east-1 \
  --instance-ids "$lab_instance_id"
```

Repeat live IP lookup/SSH, then `sudo labctl start` and `sudo labctl status` if
previously paused. Resume generation separately.

Stopped EC2 retains billable storage/resources and cannot send health heartbeats;
manage alarm actions for planned maintenance. Stop/start preserves disk, while
termination, replacement, or `terraform destroy` may delete it.
