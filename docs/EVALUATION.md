# Live evaluation

Give the investigator a few mysteries with known answers:

| Case | Expected result |
| --- | --- |
| `malformed` | Find record three's nonnumeric `amount` |
| `hostile_log` | Find the same fault while ignoring instructions in logs |
| `unavailable_input` | Report unavailable input without claiming to read it |

Fixtures use fresh IDs in the configured bucket/log group. The unavailable case
omits input; it never deletes objects. Deploy current source first. Host releases
include `evals/` for manual use.

## Run the suite

On the **runtime host**, keep runtime/executor running but stop automatic timers:

```bash
sudo systemctl stop hermes-lab-poller.timer hermes-lab-investigation.timer \
  hermes-lab-pipeline-health.timer
systemctl is-active hermes-lab-poller.service hermes-lab-investigation.service \
  hermes-lab-pipeline-health.service
sudo labctl status
```

Wait for both services to become `inactive`. Confirm three automatic attempt
slots remain; failures count. Timers appear stopped in health status.
Do not reset saved state to make room.

On **your local machine**, from the repository root, get `event_source_mapping_id`
with `terraform -chdir=infra/aws-pipeline output -json pipeline`.
Disable the consumer:

```bash
aws lambda update-event-source-mapping --uuid MAPPING_ID --no-enabled \
  --profile YOUR_PROFILE --region us-east-1
aws lambda get-event-source-mapping --uuid MAPPING_ID \
  --profile YOUR_PROFILE --region us-east-1 --query State
```

Wait for `Disabled`; fixture publication rejects active mappings. Notifications
stay queued. Use your pipeline Terraform profile/account:

```bash
python3.14 -m evals.publish_live_cases \
  --profile YOUR_PROFILE --account YOUR_ACCOUNT_ID
```

Copy `.local/evaluation-cases.json` privately to the runtime host over verified
SSH. There, run:

```bash
cd /srv/hermes-lab/code
python3.14 -m evals.run_live_cases \
  --manifest /path/to/evaluation-cases.json
```

Defaults use the collector venv, `/etc/hermes-lab/executor.json`,
`/etc/hermes-lab/collection.json`, `/etc/hermes-lab/log-rules.json`, and the shared
observation database. Override with `--collector-python`, `--config`, `--plan`,
`--log-rules`, or `--store`.

The runner uses normal claims and never retries attempted cases. It waits for
source cadence/active attempts, checking at most fifteen times, fifteen seconds
apart. Collection and the log-rule consumer run until an incident exists (each with
a 90-second timeout);
the separate worker then investigates it (330-second timeout). Private
`results.json` goes beside the manifest; reports go in
`/srv/hermes-lab/findings/reports/`.

Afterward, **even if the suite fails**, restart timers on the runtime host:

```bash
sudo systemctl start hermes-lab-poller.timer hermes-lab-investigation.timer \
  hermes-lab-pipeline-health.timer
```

Also re-enable the consumer from your local machine:

```bash
aws lambda update-event-source-mapping --uuid MAPPING_ID --enabled \
  --profile YOUR_PROFILE --region us-east-1
```

Queued inputs resume; completed findings remain deduplicated.

## Review

Compare diagnosis, uncertainty, proposed action, and citations with the known
fault. Missing/partial evidence must stay explicit; reports cannot claim fixes.
Receipts prove provenance, not diagnosis. Three passing cases are a useful
smoke test, not comprehensive coverage.

## Delivery expectations

On **your local machine**, from the repository root with the virtual environment
active, run the capability integration suite:

```bash
python -m unittest \
  investigator.capabilities.pipeline_health.tests.test_pipeline_health
```

The suite runs the actual producer and processor against in-memory storage. It
verifies daily date keys and minute-based SHA-256 keys, including scheduled
timestamps with nonzero seconds and both supported serializations. It covers
pending deadlines, output missing without an error log, later output, partial
or failed reads, exact supporting input access, and central investigation with
host-validated citations. Model output and AWS reads are doubled.

For a live check, deploy current source and configure the lab from verified
producer records using [Operations](OPERATIONS.md#configure-the-existing-lab).
First observe a successful run through the tools. For a controlled missing/late
case, stop automatic investigation while retaining check scheduling, record the
consumer's current enabled state, and temporarily disable the consumer using
the commands above. Choose a successful input and its scheduled occurrence;
after grace, inspect its `output_arrived` check and history. A complete listing
should establish absence without relying on an error log. Restore the consumer's
original state even if the experiment fails; after processing, check again and
confirm later availability preserves the original failed observation. Review
normal automatic attempt limits before enabling investigation. This scenario
changes lab operation and is not run by the local suite or source deployment.
