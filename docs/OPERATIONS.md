# Operating Hermes Lab

Your day-to-day controls: start the lab, follow the evidence, and pause when
you're done. The agent and synthetic pipeline have separate switches.

“Your local machine” is your computer; the “runtime host” runs the collector
and investigator. The local container and EC2 use the same Linux/systemd
services. Use the Make targets below for your local container; the SSH workflow
connects to EC2.

## Local container controls

Run these from the repository root on your local machine after
[local setup](SETUP.md#local-docker-runtime):

| Action | Command |
| --- | --- |
| Build and start the runtime host | `make up` |
| Inspect services, health, and budget | `make status` |
| Follow service logs | `make logs` |
| Pause workers and Hermes containers | `make stop` |
| Resume after a pause | `make start` |
| Restart services | `make restart` |
| Authenticate Hermes | `make login` |
| Investigate interactively | `make chat` |
| Open a runtime host shell | `make shell` |
| Stop the host, preserving volumes | `make down` |

Pass command options with `ARGS`, for example `make chat ARGS="--minutes 15"`.
Inside `make shell`, use the same `labctl`, `systemctl`, `journalctl`, and
`/srv/hermes-lab/` paths documented for the runtime host. To configure YAML,
edit it there and run `labctl restart` to validate and compile it.

The runtime uses a snapshot of the selected profile’s credentials. SSO and
role sessions retain their provider expiry; static keys have no session expiry.
Refresh the snapshot after login, expiry, or key rotation from your local
machine with an explicit profile:

```bash
make credentials AWS_PROFILE=YOUR_PROFILE
```

The command replaces the trusted credential file without restarting workers or
resetting budgets. It revalidates the configured account and principal; a
different principal requires `make configure` and `make up`. Expired or missing
credentials fail closed. Refresh your SSO login first if necessary. Hermes
authentication is separate from AWS authentication.

After changing resource allowlists or optional role settings in
`.local/runtime.yaml`, run `make configure AWS_PROFILE=YOUR_PROFILE`, then
`make up` to activate the reviewed configuration.

`make down` keeps OAuth, findings, observations, executor history, and nested
Docker images in project-specific named volumes. A later `make up` preserves
your pause choice. Checkpoints use `labctl checkpoint` inside `make shell`.
Local volumes have no AWS Backup coverage; see
[local saved state](RECOVERY.md#local-container-saved-state).

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

On the **runtime host**, edit the source catalog and consumer definitions:

```bash
sudoedit /srv/hermes-lab/observations.yaml
sudoedit /srv/hermes-lab/log-rules.yaml
```

The catalog names approved feeds; log rules and pipeline health each own their
subscriptions. See [configuration and coverage](OBSERVATION_CONFIG.md). Finish
an active investigation or chat, then validate and activate the whole graph:

```bash
sudo labctl restart
sudo labctl status
```

Restart validates YAML, host scope, subscriptions, and regex compilation before
stopping services. Rejected definitions leave active compiled configuration and
pause choice intact. Restart revokes sessions. Initial installation resolves
the template's deployed location; later starts preserve your installed YAML.
`--observations /absolute/path/catalog.yaml` selects a replacement catalog.

Preview one text block on the runtime host, or use your local machine's venv and
local paths:

```bash
/srv/hermes-lab/collector-venv-py314/bin/python \
  -m investigator.consumers.log_rules.preview \
  --definition /srv/hermes-lab/log-rules.yaml \
  --source processor_logs --text /tmp/example-log-block.txt
```

Preview returns matches and character spans without AWS or model calls.
`findings/last-poll.json` reports source progress and collection errors;
`findings/last-log-rules.json` reports consumer progress. Collection audit is
`findings/collection-audit.jsonl`. Raw shared evidence and coverage are in
`/var/lib/hermes-lab-observations/observations.sqlite3`, outside the agent.
Rules replay saved evidence when their revision changes. Keep saved state private.

## Configure delivery expectations

On the **runtime host**, edit pipeline expectations and their investigation
context, then activate the same shared configuration graph:

```bash
sudoedit /srv/hermes-lab/pipeline-health.yaml
sudo labctl restart
cat /srv/hermes-lab/findings/last-pipeline-health.json
```

Use `consumer.enabled` to control this consumer and subscriptions to request
shared evidence. S3-only checks need no log subscription. See
[expectation syntax](OBSERVATION_CONFIG.md#pipeline-delivery-expectations).
Restart validates and compiles all three files before activating services;
there is no AWS or model call during configuration. Finish active chat or
investigation first, because restart revokes sessions.

The scheduler stops with `labctl pause`. Disabled consumers perform no checks
and add no collection requirements. The pipeline-health tool package must also
be enabled for scheduled pipelines. Chat uses the same configured checks;
private check history is in
`/var/lib/hermes-lab-executor/pipeline-health.sqlite3`.

### Configure the existing lab

The lab needs no producer or processor changes. Its output key is
`output/<sha256-of-literal-scheduled_at-first-24-hex>.json`. Verify the actual
scheduled timestamp representation and interval phase before enabling checks.
An AWS rate schedule without an explicit start date does not establish a
minute-boundary anchor. AWS documents the
[scheduled-time context attribute](https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-schedule-context-attributes.html)
and [rate schedule start behavior](https://docs.aws.amazon.com/scheduler/latest/UserGuide/schedule-types.html).

On **your local machine**, from the repository root, save current pipeline
outputs in your ignored local directory:

```bash
mkdir -p .local
terraform -chdir=infra/aws-pipeline output -json pipeline > .local/pipeline.json
```

After reviewing/applying the pipeline Terraform output changes, use the returned
producer log group and region to retrieve two consecutive scheduled invocations.
Use your intended AWS profile and verified account; `START_TIME_MS` should select
recent runs from the current schedule:

```bash
aws logs filter-log-events --log-group-name PRODUCER_LOG_GROUP \
  --filter-pattern '{ $.event = "invocation_received" }' \
  --start-time START_TIME_MS --limit 2 --no-paginate \
  --profile YOUR_PROFILE --region YOUR_REGION > .local/producer-records.json
```

Create your YAML using the
[minute-based example](OBSERVATION_CONFIG.md#minute-based-lab-example).
Set the interval from `schedule_expression`, and use two consecutive producer
records to verify its phase. The example's `anchor` is illustrative: replace it
with a recorded UTC occurrence, retaining its seconds. Use `utc_seconds` for
literal timestamps ending in `Z`, or `utc_milliseconds` for `.000Z`.

Check each recorded `run_id` against the first 24 hexadecimal characters of the
SHA-256 digest of its exact `scheduled_at` text. On **your local machine**:

```bash
printf '%s' 'RECORDED_SCHEDULED_AT' | shasum -a 256
```

Do not round the timestamp or add a newline before hashing. Confirm that the
records are one minute apart for `every: 1m`. Set grace, supporting sources,
and investigation instructions to your expectations. Configuration validation
checks supported syntax and approved resources; it cannot verify that a naming
convention matches your live producer.

Copy that YAML privately to the runtime host, then use the configuration command
above with `--source /path/to/pipeline-health.yaml`. Review/apply the agent plan
and deploy current source first: trusted resource profiles come from its outputs.
The anchor begins automatic coverage at the first verified occurrence. If the
schedule is recreated or its timestamp format changes, update the definition
from current records. A changed convention must not be diagnosed as missing data.

## Restart collection from a new point

For a fresh experiment, **on the runtime host**:

```bash
sudo labctl reset-collector --since now
```

Reset reports its archive path under
`/srv/hermes-lab/checkpoints/collector-reset-*/`. It archives findings, reports,
queue, and shared observation database while retaining attempts/budgets, login,
and pause choice. A running
lab resumes with installed YAML.

To install edited YAML and read the last fifteen minutes:

```bash
sudo labctl reset-collector --since 15m \
  --observations /srv/hermes-lab/observations.yaml
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
