# AWS access and tools

Chat and automatic investigations use a host executor for bounded, read-only
AWS access. Background collection uses separate deadlines and audit, sharing
approved source reads between consumers. Trusted configuration selects an
explicit AWS profile, EC2 instance credentials, or a private credential file.
Any source can optionally assume a configured role. The shared `aws_access/`
provider resolves credentials, freezes each AWS call environment, and verifies
the allowlisted account and configured principal before evidence reads.

Local setup exports your explicit profile with AWS CLI on your local machine.
A disposable setup container receives only the export and approved resource
configuration. It validates the account and principals and optionally assumes
the configured role before writing the runtime snapshot. AWS profile files and
login caches stay on your local machine. Trusted services inside the host read
the credential snapshot from
`/run/hermes-lab-credentials/aws.json`; Hermes and its tools cannot read it.
The snapshot is owned by root and readable by host group 1000. The collector
retains that supplementary group while using the executor group to access
the observation database; the executor also has snapshot access.
Configuration pins the principal's stable AWS identifier. Refresh revalidates
both source and final principals and retains authorization and budget history.
A missing or expired file cannot fall back to instance metadata or a default
profile. EC2 configuration selects instance credentials and the scoped read
role. The local host marker controls health behavior only.

## Request flow

1. The host opens an approved chat profile or incident scope.
2. A plugin sends arguments with a private token kept outside model prompts.
3. The executor checks scope, types, expiry, and budget, audits authorization,
   and authenticates the configured AWS principal.
4. An immutable plan selects a fixed operation within the approved resources.
5. The host validates and bounds results, rechecks expiry/revocation, and saves
   an audit record and receipt before releasing evidence. Failed checks withhold it.

The EC2 deployment restricts read-role trust to the host role. Its IAM policy
allows configured S3/log reads and optional CloudTrail, SQS, and Lambda metadata
reads. A direct local profile uses its existing IAM permissions. A dedicated
role adds an AWS-enforced ceiling when you want one. In either mode, trusted
code enforces the same resource allowlists, operations, budgets, audit, and
evidence receipts; the model cannot select credentials or widen policy.
Writes, account-wide discovery, log unmasking, message operations, function
invocation, and code download remain outside investigative tool access. Health
and backup permissions are separate.

## Tool packages

Native Hermes plugins contain schemas, registration, and standalone socket clients.
Trusted service classes are registered in `tool_boundary/registered.py`.

| Package | Source | Toolset |
| --- | --- | --- |
| CloudWatch Logs | `investigator/tools/aws_cloudwatch/` | `aws_cloudwatch` |
| CloudTrail history | `investigator/tools/aws_cloudtrail/` | `aws_cloudtrail` |
| SQS queue attributes | `investigator/tools/aws_sqs/` | `aws_sqs` |
| Lambda settings | `investigator/tools/aws_lambda/` | `aws_lambda` |
| S3 and resource inventory | `investigator/tools/hermes_lab_aws/` | `hermes_lab_aws` |
| Pipeline delivery checks | `investigator/capabilities/pipeline_health/tools/` | `pipeline_health` |

Select packages in `investigator/runtime/config.yaml`:

```yaml
plugins:
  enabled:
    - hermes-lab-aws
    - hermes-cloudwatch
    - hermes-cloudtrail
    - hermes-pipeline-health
    - hermes-sqs
    - hermes-lambda
  entries:
    hermes-cloudtrail:
      resource_names:
        - your-function-name
```

S3 and CloudWatch are enabled by default. `plugins.disabled` takes precedence;
unknown packages or options are rejected. CloudTrail requires exact resource
names. SQS/Lambda require Terraform inputs `sqs_queue_arns`,
`lambda_function_arns`, and `lambda_event_source_mappings`. ARNs must belong to
the deployment account/region; mapping UUIDs bind an approved function and queue.
Each allowlist accepts at most fifty entries. Queue URLs are derived on the host.

Review/apply the agent Terraform plan before deploying. Deployment compares
normalized YAML and host selections. Full activation generates a read-only
`tool-packages.json` manifest; the model-side selector accepts only host-enabled
packages with matching options. Partial executor/plugin deployment does not
change package selection. Disabled tools are rejected before reserving budget.

SQS/Lambda reads work in chat and run/log investigations. Pipeline-health
investigations retain their configured delivery scope: exact evidence and
supporting keys, plus approved logs from five minutes before delivery through
five minutes after its deadline. Its host extension is installed explicitly;
protected scheduler operations are never model tools.

## Host tool organization

Services implement `ToolProvider` in `tool_boundary/services/base.py`:

| Part | Responsibility |
| --- | --- |
| Metadata and options | Package/toolset names, tools per scope, plugin paths, normalized YAML/host settings |
| `validate_scope` | Validate context fields owned by the service |
| `authorize` | Validate arguments and resolve a scoped read |
| `request` | Construct a fixed, bounded transport request |
| `normalize` | Validate provider results, select safe fields, and issue references |
| `read` / `describe` | Execute a read or describe approved resources |

Each service owns its operation names, argument maps, resource validation,
response projections, and reference formats. Shared AWS identity parsing lives
in `providers/aws_identity.py`; credentials and CLI execution in `providers/aws.py`.
The registry validates applicable enabled services on scope binding and rechecks
the selected service per call. Capability scope checks delegate to the installed
extension. The boundary imports no investigator code.

`scopes.py` and `loader.py` create trusted contexts. Policies reserve budgets;
`executor.py` manages sessions, audit, and receipts; `results.py` bounds evidence;
`receipts.py` resolves exact citations. Contexts validate their identifiers and
time ranges; `validation.py` holds only generic helpers.

### YAML, host policy, and model selection

Package classes normalize `plugins.entries` and their corresponding trusted host
settings. Resource allowlists come from reviewed deployment outputs. The model
receives only plugins, the read-only manifest, and `selection.py`, without host
policy or service implementations.

To add a tool package, implement its service class and model plugin, register it,
and provide reviewed IAM/host scope. Registration supplies plugin paths for
mounts and archives. The shared executor needs no operation-specific branches.

## Tools and policies

| Scope | Tool | Result |
| --- | --- | --- |
| Resource | `list_aws_resources` | Configured targets and limits; no AWS discovery |
| Resource | `describe_cloudwatch_scope` | Approved log group/window; no AWS call |
| Resource | `describe_cloudtrail_scope` | Approved resource names/window; no AWS call |
| Resource | `describe_sqs_scope` | Approved queue ARNs and limits; no AWS call |
| Resource | `describe_lambda_scope` | Approved functions and mapping bindings; no AWS call |
| Resource | `search_log_events` | One approved group/window page |
| Resource | `list_s3_objects` | One page within an approved bucket prefix |
| Resource | `read_s3_object` | Text sample of an approved object |
| Incident | `search_run_logs` | Nearby events filtered by run ID |
| Incident | `list_run_objects` | Expected run objects or attempt directory |
| Incident | `read_run_object` | Input, output, or known attempt-log sample |
| Log match | `search_log_events` | Approved group within ±5 minutes of the event |
| Resource, incident, log match | `lookup_cloudtrail_events` | One approved resource's management-event page within the session window |
| Resource, incident, log match | `get_sqs_queue_attributes` | Selected queue settings and approximate current counts |
| Resource, incident, log match | `get_lambda_function_configuration` | Selected current function settings; environment values and code withheld |
| Resource, incident, log match | `get_lambda_function_concurrency` | Current reserved concurrency; zero differs from no reservation |
| Resource, incident, log match | `get_lambda_event_source_mapping` | One approved SQS-to-Lambda mapping's current settings |

CloudTrail covers 90 days of management-event history, excluding data events,
Insights, and Lake. Each call checks account, region, time, and resource identity.
Empty or incomplete results cannot establish that no change occurred.

SQS counts are approximate and cannot prove message-level delivery. Lambda
settings describe the current observation, not historical configuration. No
reserved concurrency does not mean unlimited capacity. These tools do not
retrieve metric history. Responses exclude environment values, code, queue
policy documents, and mapping filter criteria; configured redrive policy is included.

Unknown fields, wrong types, scope/resource overrides, expired contexts, and
exhausted budgets are rejected. Plugin schemas grant no authority and there is
no direct AWS fallback. Log-match sessions deny S3/run tools; the worker control
socket cannot open chat profiles or caller-selected resources.

## Current limits

| Limit | Incident | Lab chat |
| --- | --- | --- |
| Lifetime | Five minutes | Default/maximum thirty minutes |
| Authorized calls | Ten | Sixty across all turns, including inventory |
| Reserved evidence | 1 MiB | 4 MiB across all turns |
| Log window | Default ±5 minutes; maximum ±15 | Default two-hour lookback; maximum 24 hours |

Chat's window spans launch minus lookback through expiry. Each request defaults
to 64 KiB, capped at 256 KiB of serialized data. Pages contain at most twenty S3
keys, two hundred CloudWatch events, or fifty CloudTrail events. AWS pagination
and retries are disabled. The thirty-second deadline includes authentication
and cannot exceed session expiry.

Authorized calls reserve the full byte allowance, including failed reads;
invalid requests reserve nothing. Credential refresh preserves accounting.
Revocation denies queued calls, cancels active reads, and withholds their output.
Context loss or executor restart ends access and clears credentials and receipts.

## Evidence and findings

Successful results include evidence IDs, issued references, source parameters,
observation time/type, a payload hash, and truncation state. Clients check size
and hash. Logs and objects remain untrusted evidence, including embedded instructions.

Automatic citations must match an ID/reference pair from a successful read in
the current authorized session. Invented IDs, mismatched references, and failed
reads are rejected. Receipts prove what was returned, not whether the diagnosis
is correct. Partial results and failed reads remain report limitations.

`Finding` validates report structure; `FindingParser` uses the common
`EvidenceVerifier` interface to attach trusted receipts through
`SessionEvidenceVerifier`. Tool classes own reference formats; the parser treats
them as opaque and needs no new branches when a tool is added.

Lists expose only entries with issued references: at most twenty references and
4 KiB per call. Clipping trims corresponding data and marks it partial. Empty
event lists provide no event citation. Only an exact, complete empty object
listing supports absence; a failed read or incomplete page cannot. S3 completeness
requires matching bucket/prefix, `IsTruncated: false`, consistent `KeyCount`,
and no continuation or grouped-prefix results. Missing metadata is partial;
contradictory metadata fails validation. See
[ListObjectsV2](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html).

## Isolation and audit

The model sees a read-only request socket directory, without instance credentials,
host Docker, control endpoints, original evidence, or writable policy. The shell
container has no executor socket; investigative toolsets exclude shell/file tools.
Tool SSH stays internal on a separate network.

| Host endpoint | Access |
| --- | --- |
| `/run/hermes-lab-executor/control.sock` | Root peer UID; socket `0600`, parent `0700` |
| `/run/hermes-lab-collector/control.sock` | Creator UID 1000 or root; incident scopes only; directory `0750`, socket `0660` |
| `/run/hermes-lab-executor/requests/requests.sock` | Directory `0750`, socket `0660`; shared with model GID 10000 |

AWS service calls use a fresh environment containing the validated credential
snapshot, with profile lookup and instance metadata disabled. Profile resolution
happens only in trusted code when explicitly configured. `/var/lib/hermes-lab-executor/audit.jsonl` records trusted
decisions, source parameters, counts, hashes, and creator UID, excluding tokens,
credentials, raw requests, and evidence bodies. Rotation keeps the current
1 MiB file plus three older files. Failed audit writes withhold access/results.

## Design rationale

Tool visibility and schemas are separate from authorization. Trusted host code
checks the requested scope, execution, and returned evidence. Policy, audit,
expiry, and provenance failures deny access; retries cannot bypass reservations.

## Security scope and next steps

Trust rests with the host, installed packages, orchestrator, deployment policy,
and AWS. Backend interfaces are not sandboxes. Agent-selected Python plugins
and model access to control operations are unsupported.

This is a single-host boundary, without per-person or tenant authentication.
A stolen token plus socket access exposes its remaining scope and budget.
Provenance does not establish truth or prevent prompt injection; there is no
general redactor. Local audit rotation cannot protect against a compromised
host. Shared/remote clients require independent authentication, and write tools
would require explicit action approval and durable outcome tracking.

## Shared observation access

The collector writes `/var/lib/hermes-lab-observations/observations.sqlite3`;
consumers read bounded batches with independent progress. The pipeline-health
executor has read-only access; the model has no database mount. Expectations
supply query source, correlation, and time window. Incomplete coverage stays unknown.

`collection_log_groups` approves exact groups in the host account/region. Each
log incident remains bound to its original group and window. CloudTrail uses
the enabled package and exact resource allowlist, without data-event or archive access.
