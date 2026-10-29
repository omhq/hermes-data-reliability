# Operating Hermes Lab

Your day-to-day controls: start the lab, follow the evidence, and pause when
you're done. The agent and synthetic pipeline have separate switches.

“Your local machine” is your computer; the “runtime host” runs the collector
and investigator. Commands below use the current Linux/systemd deployment.

## Connect from your local machine

Run commands on your local machine from the repository root:

```bash
cd /path/to/hermes-data-reliability
```

Replace profile, region, and key placeholders with your agent Terraform inputs
(`infra/aws-agent/terraform.tfvars`). Keep keys, variables, and state out of Git.
If SSO has expired:

```bash
aws sso login --profile YOUR_PROFILE
```

Get the instance ID from Terraform and its live IP from AWS. Stop/start can
change the IP:

```bash
lab_instance_id=$(terraform -chdir=infra/aws-agent output -raw instance_id)
lab_ip=$(aws ec2 describe-instances \
  --profile YOUR_PROFILE --region us-east-1 \
  --instance-ids "$lab_instance_id" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
printf 'SSH destination: ec2-user@%s\n' "$lab_ip"
```

If the IP is empty or `None`, check instance state. Otherwise:

```bash
ssh -i ~/.ssh/experiment.pem \
  -o IdentitiesOnly=yes \
  -o UserKnownHostsFile=.local/known_hosts \
  -o StrictHostKeyChecking=yes \
  "ec2-user@$lab_ip"
```

For port-22 timeouts, compare your public IPv4, `ssh_cidr`, and the live security
group. Review a plan before changing the trusted `/32`.

For a first or replacement connection, verify the fingerprint against the EC2
console system log. Create `.local/`, use `StrictHostKeyChecking=ask` once, then
`yes`. Never bypass checks or blindly dismiss warnings.

Terraform also has cached addresses:

```bash
terraform -chdir=infra/aws-agent output -raw public_ip
terraform -chdir=infra/aws-agent output -raw ssh_destination
```

Prefer live lookup after stop/start. After replacement, use current Terraform
state for the new instance ID.

## Stop, start, and check the agent

| Action | Command |
| --- | --- |
| Check runtime, timers, health, and budget | `sudo labctl status` |
| Pause workers and stop both containers | `sudo labctl pause` |
| Resume | `sudo labctl start` |
| Restart | `sudo labctl restart` |
| Investigate through chat | `sudo labctl chat` |

`stop` aliases `pause`. Use `labctl` to coordinate services; pause choice,
results, and login survive reboot. Status shows budget `used`, `remaining`,
and `next_available_utc`.

Collection continues during chat; automatic investigations wait without spending
attempts. Pause keeps health/backups active and does not stop hosting charges.
It is a healthy state, but synthetic generation keeps running until you disable
it separately.

## Investigate through chat

On the runtime host, start the lab, then run `sudo labctl chat`. Try:

> Find failed loads in the last two hours, inspect their S3 input and attempt
> logs, and explain the failures with evidence.

For a different window or an opening question, also on the runtime host:

```bash
sudo labctl chat --minutes 15 --lookback-hours 6
sudo labctl chat --query "Show my approved AWS resources."
```

Follow-ups share a budget. `/exit`, disconnect, or expiry ends access; continuing
from history needs a new authorized launch. `--profile lab` selects host access,
not an AWS login. Over SSH, allocate a terminal: `ssh -t ... 'sudo labctl chat'`.

## Pause or resume synthetic data generation

On your local machine, edit ignored `infra/aws-pipeline/terraform.tfvars`:

```hcl
pipeline_schedule_enabled = false # true to resume
```

Review and apply the saved plan:

```bash
terraform -chdir=infra/aws-pipeline plan -out=pipeline.tfplan
terraform -chdir=infra/aws-pipeline show pipeline.tfplan
terraform -chdir=infra/aws-pipeline apply pipeline.tfplan
```

A schedule-only plan should change only Scheduler state; resolve other changes
first. The example defaults to `false`. Verify live state in AWS; cached Terraform
outputs may be stale.

## Read logs and results

For the following AWS commands, use **your local machine** and the profile/region
in `infra/aws-pipeline/terraform.tfvars`. Agent commands use the agent root's inputs.

Follow producer uploads:

```bash
aws logs tail /aws/lambda/hermes-lab-producer \
  --profile YOUR_PROFILE --region us-east-1 \
  --since 10m --format short --follow
```

Follow processor/SQS events (`hermes-lab-loader`):

```bash
aws logs tail /aws/lambda/hermes-lab-loader \
  --profile YOUR_PROFILE --region us-east-1 \
  --since 10m --format short --follow
```

Ctrl-C stops following. Correlate UTC JSON logs by `run_id`, then processor
SQS/attempt IDs. Trace uploads through loading, validation, writes, and completion.
The final per-file `status` drives detection.

From the repository root on your local machine, get deployment identifiers:

```bash
terraform -chdir=infra/aws-pipeline output -json pipeline
```

Use `queue_url` or `dead_letter_url` to inspect without consuming messages:

```bash
aws sqs get-queue-attributes --queue-url QUEUE_URL \
  --profile YOUR_PROFILE --region us-east-1 \
  --attribute-names ApproximateNumberOfMessages \
    ApproximateNumberOfMessagesNotVisible ApproximateNumberOfMessagesDelayed
```

Counts are approximate; five receives send failed messages to the DLQ.
Disabling generation leaves queued work processing. `labctl pause` stops neither.

On the **runtime host**, inspect worker and health logs:

```bash
sudo journalctl -u hermes-lab-poller.service -n 50 --no-pager
sudo journalctl -u hermes-lab-investigation.service -n 50 --no-pager
sudo journalctl -u hermes-lab-health.service -n 20 --no-pager
sudo systemctl list-timers 'hermes-lab-*' --all --no-pager
```

Add `-f` to follow; Ctrl-C exits. Host journal entries use UTC time, level,
and module; pipeline JSON logs retain `level`/`logger`.
View findings:

```bash
ls -lt /srv/hermes-lab/findings/reports/
tail -n 5 /srv/hermes-lab/findings/incidents.jsonl
```

| Path on the runtime host | Contents |
| --- | --- |
| `/srv/hermes-lab/findings/incidents.sqlite3` | Incidents, progress, attempts; authoritative database |
| `/srv/hermes-lab/findings/incidents.jsonl` | Exported incidents/findings |
| `/srv/hermes-lab/findings/reports/` | Completed reports |
| `/srv/hermes-lab/evidence/` | Evidence snapshots |

Each worker's timer ticks 15 seconds after it finishes. Sources follow their
YAML cadence; backlog/errors retry next collection tick. `last-poll.json` tracks
collection; `last-investigation.json` tracks investigations and budget.
Hermes runs only for eligible incidents, up to three attempts per rolling hour.
A running container may be idle. Emails contain completed investigations;
CloudWatch health alarm emails are disabled.

To pause investigations while collection continues, on the runtime host:

```bash
sudo systemctl stop hermes-lab-investigation.timer hermes-lab-investigation.service
```

Incidents stay queued. Resume with
`sudo systemctl start hermes-lab-investigation.timer`. This pause ends at reboot;
`labctl pause` persists for all workers. Health reports the stopped timer.

To retry one pending incident, on the runtime host:

```bash
cd /srv/hermes-lab/code
sudo -u ec2-user /usr/bin/python3.14 -m investigator.runtime.investigation_worker \
  --config /etc/hermes-lab/executor.json --retry-incident INCIDENT_ID
```

This skips backoff only. Hourly/incident limits and live leases still apply.
The collector CLI has no investigation or retry options.

## Agent report emails

Set `alert_email` in your local machine's ignored agent `terraform.tfvars`,
review/apply the plan, and [deploy](SETUP.md). Confirm a new SNS subscription
by email; unchanged subscriptions are reused.

On the runtime host, check delivery:

```bash
sudo systemctl status hermes-lab-notifications.timer --no-pager
sudo journalctl -u hermes-lab-notifications.service -n 20 --no-pager
```

The first scan skips historical reports. New completed investigations queue
explanation, proposed action, uncertainty, and compact metadata. Delivery follows
`labctl pause`/`start`. Health stays local/CloudWatch, without health or recovery
emails. A stopped or failing agent cannot write a new investigation report.

To send a historical report once, on the runtime host:

```bash
cd /srv/hermes-lab/code
sudo -u ec2-user /usr/bin/python3.14 -m investigator.runtime.notifications \
  --send-incident INCIDENT_ID
```

Already-sent reports are not requeued. Attempts and sanitized errors go in
`/srv/hermes-lab/findings/notifications.sqlite3`. See
[delivery rules](ARCHITECTURE.md#health-and-storage) for retries and duplicates.

## Configure the collector

On the **runtime host**, from `/srv/hermes-lab/code`, supply a definition:

```bash
sudo labctl start \
  --observations /srv/hermes-lab/code/investigator/runtime/observations.yaml
sudo labctl status
```

The runtime validates against host policy, atomically installs
`/srv/hermes-lab/observations.yaml`, then starts services. Use an absolute path;
`restart` also accepts `--observations`. Rejected definitions leave services and
pause choice intact. Later starts/reboots reuse the installed copy. Region/group
must match the allowlist; only initial defaults resolve deployment locations.

`investigator/runtime/start.sh` forwards to `labctl start`. The poller reads
installed YAML outside the model container. For later edits on the runtime host:

```bash
sudoedit /srv/hermes-lab/observations.yaml
/srv/hermes-lab/collector-venv-py314/bin/python -m collector.poll \
  --config /etc/hermes-lab/executor.json \
  --observations /srv/hermes-lab/observations.yaml --validate-only
```

Validation checks structure, location, and pattern compilation without AWS/model
calls. Valid edits load next invocation. Use `investigation.instructions` for
shared prompts, with per-source overrides.

Preview one text block on the runtime host, or use your local machine's venv with
`requirements.txt` and local paths:

```bash
/srv/hermes-lab/collector-venv-py314/bin/python -m collector.preview \
  --observations /srv/hermes-lab/observations.yaml \
  --source processor_logs --text /tmp/example-log-block.txt
```

Each file is one block. Preview returns rules, severities, and character spans
without AWS/model calls.

`/srv/hermes-lab/findings/last-poll.json` reports source status, cursor, next poll,
sanitized errors, volume, and lag. SQLite keeps originals/revisions;
`collection-audit.jsonl` holds the host audit. Rule edits may replay history
without reopening completed incidents or resetting attempts. Keep findings
and saved state private.

## Restart collection from a new point

For a fresh experiment, **on the runtime host**:

```bash
sudo labctl reset-collector --since now
```

Reset reports its archive path under
`/srv/hermes-lab/checkpoints/collector-reset-*/`. It archives findings, reports,
and queue while retaining attempts/budgets, login, and pause choice. A running
lab resumes with installed YAML.

To install edited YAML and read the last fifteen minutes:

```bash
sudo labctl reset-collector --since 15m \
  --observations /srv/hermes-lab/code/investigator/runtime/observations.yaml
```

`--since` defaults to `now`; it also accepts `s`/`m`/`h` up to `24h`. The saved
boundary survives restarts and limits overlap. YAML validates before services
stop. Finish active investigations or pause first. Never delete lock files;
ordinary edits need no reset.

## Stop or start an EC2 instance

Disable generation to avoid new backlog, then run `sudo labctl pause` on the
runtime host. On your local machine, in the shell holding `$lab_instance_id`:

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

Repeat live IP lookup and SSH, then `sudo labctl start` and `sudo labctl status`
if paused. Resume generation separately.

A stopped instance keeps billable storage/resources and cannot send heartbeats or
reports. CloudWatch alarm actions stay disabled. Stop/start preserves disk;
termination, replacement, or `terraform destroy` may delete it.
