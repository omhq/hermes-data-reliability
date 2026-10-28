# AWS access and tools

A host executor makes bounded, read-only AWS CLI calls for chat and automatic
investigations. CloudWatch collection shares its identity checks and assumed
role, using the group approved in root-owned executor JSON.

## Request flow

1. The host opens a configured resource profile for chat or incident scope for
   automation.
2. An enabled tool package sends raw arguments and a private context token;
   tokens never enter model-visible arguments or prompts.
3. The executor validates scope, types, expiry, and budget, audits authorization,
   and assumes the investigator role for fifteen minutes.
4. Fixed CLI operations return bounded evidence and a receipt.

Investigator IAM allows only the host role to assume
the read role. Access is limited to configured resources via `s3:ListBucket`,
`s3:GetObject`, and `logs:FilterLogEvents`. The SSE-S3 bucket needs no KMS decrypt
permission. Account-wide discovery, writes, and log unmasking are excluded;
health/backup permissions are separate. Enabling CloudTrail adds
`cloudtrail:LookupEvents` in the configured region. The host restricts those
lookups to explicitly configured resource names and authorized time windows.

## Tool packages

Each service has its own native Hermes plugin package:

| Package | Source | Toolset |
| --- | --- | --- |
| CloudWatch Logs | `investigator/tools/aws_cloudwatch/` | `aws_cloudwatch` |
| CloudTrail history | `investigator/tools/aws_cloudtrail/` | `aws_cloudtrail` |
| S3 and resource inventory | `investigator/tools/hermes_lab_aws/` | `hermes_lab_aws` |

Each package contains its own registration code, schemas, and Unix-socket client.
Packages import only the Python standard library and files inside their own
folder. Copy the complete folder to use a package independently; no shared
Python package or additional import path is required.

`investigator/tool_packages.py` handles package selection and the host's tool
allowlist. Plugins do not import it. AWS execution and authorization remain on
the host.

Select packages in `investigator/runtime/config.yaml`. For example:

```yaml
plugins:
  enabled:
    - hermes-lab-aws
    - hermes-cloudwatch
    - hermes-cloudtrail
  entries:
    hermes-cloudtrail:
      resource_names:
        - your-function-name
```

CloudWatch and S3 are enabled by default. CloudTrail requires at least one exact
resource name. You can enable each package independently; `plugins.disabled`
takes precedence over `plugins.enabled`. Unknown names and invalid selections
are rejected. At least one package must be enabled to launch an investigation.

Apply configuration through the [agent setup workflow](SETUP.md): review/apply
the `infra/aws-agent` plan, then deploy the runtime. Terraform reads this YAML
to produce the trusted executor selection and optional CloudTrail IAM grant.
The runtime selects the corresponding toolsets for both chat and automation.
The host also rejects disabled tools before AWS calls or budget reservations;
editing the model's writable configuration cannot expand host access.

CloudTrail uses [LookupEvents](https://docs.aws.amazon.com/awscloudtrail/latest/APIReference/API_LookupEvents.html)
for management-event history from the last 90 days. It reads one page of at most
50 events, validates account/region/time/resource provenance, and issues evidence
receipts. Data events, Insights, and Lake queries are outside this package's
scope. Empty, filtered, paginated, and truncated results do not prove that no
relevant change occurred.

## Tools and policies

| Scope | Tool | Result |
| --- | --- | --- |
| Resource | `list_aws_resources` | Configured targets and session limits; no AWS discovery |
| Resource | `describe_cloudwatch_scope` | Approved log group and time window; no AWS call |
| Resource | `describe_cloudtrail_scope` | Approved resource names and time window; no AWS call |
| Resource | `search_log_events` | One approved group/time-window page |
| Resource | `list_s3_objects` | One page within an approved bucket prefix |
| Resource | `read_s3_object` | Text sample of an approved object |
| Incident | `search_run_logs` | Nearby events filtered by the active run ID |
| Incident | `list_run_objects` | Expected run objects or attempt directory |
| Incident | `read_run_object` | Input, output, or a known attempt-log sample |
| Log match | `search_log_events` | Approved group within five minutes before/after the matched event |
| Resource, incident, log match | `lookup_cloudtrail_events` | One management-event page for an approved resource within the session's window |

The host denies tools outside the session scope, resource/role/account/region/
endpoint overrides, unknown fields, wrong types, expired or missing contexts,
and exhausted budgets. Extra arguments reach the executor for rejection;
plugin schemas are not authorization. There is no direct AWS or shell fallback.

Log-match contexts use incident limits, bind receipts to the generic incident
ID, and deny S3/run tools. The collector control socket cannot open chat
profiles or caller-selected resources.

## Current limits

| Limit | Incident | Lab chat |
| --- | --- | --- |
| Lifetime | Five minutes | Default/maximum thirty minutes |
| Authorized calls | Ten | Sixty across all turns, including inventory |
| Reserved evidence | 1 MiB | 4 MiB across all turns |
| Log window | Default ±5 minutes; maximum ±15 | Default two-hour lookback; maximum 24 hours |

Chat's window runs from launch minus lookback through expiry. Requests default
to 64 KiB, capped at 256 KiB. Listings return one page: at most twenty keys or
two hundred CloudWatch events, or fifty CloudTrail events. AWS pagination/retries
are disabled. The thirty-second
deadline includes role assumption and cannot exceed expiry; oversized output
is terminated.

Calls reserve their full byte allowance; small responses and AWS failures do
not refund it. Invalid requests reserve nothing. `max_bytes` covers serialized
data, with metadata separate. Incoming JSON is capped at 8 KiB, the full response
at 270,336 bytes. Context loss/restart ends access without recreating tokens or
budgets; credential refresh retains accounting.

## Evidence and findings

Results include `evidence_id`, citeable `references`, source parameters,
observation time/type, payload SHA-256, and truncation state. The client verifies
size/hash. Logs and object contents remain untrusted data, including embedded
instructions.

Automatic citations pair an ID and reference from one successful response.
The host checks current-session receipts; invented IDs, mismatches, and failed
calls cannot supply evidence. Receipts prove returned observations, not diagnosis
correctness.

Only an exact, complete empty listing supports absence; listings do not prove
contents. Denied reads, partial results, and unvisited pages remain explicit.
References are capped at twenty and 4 KiB per call; clipping also marks results
partial. Reports retain failed reads and limitations.

## Isolation and audit

The model mounts only the read-only request directory at `/run/hermes-lab-aws/`;
the shell tool container has no executor socket. Neither gets instance
credentials, host Docker, control endpoints, collector state, or writable
evidence. Networks are separate; tool SSH is internal, without internet or
provider login. Investigations select the AWS toolset, not shell/file tools.

| Host endpoint | Access |
| --- | --- |
| `/run/hermes-lab-executor/control.sock` | Root peer UID; socket `0600`, parent `0700` |
| `/run/hermes-lab-collector/control.sock` | Creator UID 1000 or root; incident scopes only; directory `0750`, socket `0660` |
| `/run/hermes-lab-executor/requests/requests.sock` | Request directory `0750`, socket `0660`; shared with model GID 10000 |

The host owns contexts and budgets. A fresh AWS environment ignores saved
workstation profiles/credentials; temporary credentials stay in process memory.

`/var/lib/hermes-lab-executor/audit.jsonl` records decisions, sources, counts,
and hashes, excluding tokens, credentials, raw requests, and payloads. Rotation
keeps the current 1 MiB file plus three older files. Audit failure denies access
or withholds the result.
