# AWS access and tools

Agent tool calls pass through a host executor for bounded, read-only AWS access.
Chat and automatic investigations use the same boundary.

Collection runs separately. `collector/providers/aws/` validates account/roles
and assumes its read role as `hermes-lab-collector`. Connector classes in
`collector/connectors/` authorize configured CloudWatch groups or CloudTrail
resource history and construct their fixed read requests. The source catalog grants no
access. Named consumers share that collection; they cannot select credentials.
It has its own pagination, deadlines, and audit, using host deployment JSON.
It needs no agent investigation grant. The CloudTrail connector also checks
the host configuration enables `hermes-cloudtrail`. Both paths currently
use the same configured read role, with separate implementations.

The executor is the standalone [`tool_boundary`](../tool_boundary/README.md)
package: Python 3.14, standard library only. Its AWS adapter needs the installed
AWS CLI; the service needs Linux, protected sockets, and IAM/network isolation.

## Request flow

1. The host opens an approved resource profile for chat or scope for an incident.
2. An enabled plugin sends raw arguments and a private context token. Tokens
   never enter model-visible arguments or prompts.
3. The executor checks scope, types, expiry, and budget, audits authorization,
   and assumes the investigator role for fifteen minutes.
4. An immutable plan selects a fixed CLI operation. The backend rechecks
   account, region, operation, and single-page limits.
5. The host bounds and validates output, audits completion, rechecks expiry and
   revocation, and saves a detached receipt before releasing evidence.
   Failed checks withhold results.

Only the host role can assume the read role. IAM permits `s3:ListBucket`,
`s3:GetObject`, and `logs:FilterLogEvents` on configured resources. SSE-S3 needs
no KMS decrypt permission. Writes, account-wide discovery, and log unmasking
are excluded; health/backup permissions are separate. Optional
`cloudtrail:LookupEvents` access is region-bound, with host checks for exact
resource names and authorized windows.
Optional SQS and Lambda packages grant only queue-attribute and function/mapping
configuration reads on explicit resource ARNs. They grant no message receive,
send, delete, function invocation, code download, or configuration-write access.

## Tool packages

Each native Hermes plugin owns its registration, schemas, and Unix-socket client.
Each imports only its own files and the standard library; copy the whole folder
to reuse it.

| Package | Source | Toolset |
| --- | --- | --- |
| CloudWatch Logs | `investigator/tools/aws_cloudwatch/` | `aws_cloudwatch` |
| CloudTrail history | `investigator/tools/aws_cloudtrail/` | `aws_cloudtrail` |
| SQS queue attributes | `investigator/tools/aws_sqs/` | `aws_sqs` |
| Lambda settings | `investigator/tools/aws_lambda/` | `aws_lambda` |
| S3 and resource inventory | `investigator/tools/hermes_lab_aws/` | `hermes_lab_aws` |
| Pipeline delivery checks | `investigator/capabilities/pipeline_health/tools/` | `pipeline_health` |

`tool_boundary/registered.py` registers the installed service classes. Their
metadata defines package names, toolsets, tool names, and plugin directories.
`catalog.py` normalizes selections; `loader.py` creates configured host
instances. Plugins remain independent socket clients.

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

S3 and CloudWatch are enabled by default. CloudTrail needs at least one exact
resource name. Packages work independently; `plugins.disabled` wins over
`plugins.enabled`. Unknown names, unknown `plugins.entries` packages, and
unsupported package options are rejected. Entries cannot select a Python module,
class, command, endpoint, or credentials.
An investigation needs at least one enabled package.

SQS and Lambda are optional. Set `sqs_queue_arns`, `lambda_function_arns`, and
`lambda_event_source_mappings` in the agent Terraform inputs before enabling
their packages. All ARNs must belong to the deployment account and region;
functions are unqualified ARNs. Each mapping UUID explicitly binds an approved
function to an approved SQS queue. Scope comes from trusted deployment outputs,
not model arguments. Each allowlist accepts at most fifty entries. The model
selects an existing approved ARN or UUID; it cannot discover arbitrary resources
or choose an API, endpoint, qualifier, or queue URL. Queue URLs are derived on
the host from approved ARNs.

Pipeline health installs a trusted host extension explicitly through
`investigator.capabilities.pipeline_health.server`. Its Hermes plugin contains only the
schemas and socket client. YAML is validated and compiled before activation;
the model cannot edit that definition or select host Python code. Each presence
check reserves one tool call and makes at most one bounded S3 listing. Chat
checks and scheduled checks share that implementation. The scheduler's control
operations are available only on protected host sockets, never as model tools.

An automatic health incident binds a pipeline revision, named expectation,
scheduled timestamp, and approved resource identity to its exact evidence key.
It can read that key, explicitly configured supporting keys for the same
delivery, and the approved log group from five
minutes before the scheduled delivery through five minutes after its deadline.
It cannot select another delivery or browse neighboring S3 keys. Log responses
still use the normal scope/output validators. A check receipt records its
observation time and result; current-session receipts for history retain each
original observation time inside the returned data.

Use the [setup workflow](SETUP.md): review/apply the agent Terraform plan, then
deploy. Terraform turns this YAML into a trusted tool selection and optional
CloudTrail IAM grant. Chat and automation use the selected toolsets. The host
rejects disabled tools before reserving budget or calling AWS; model-writable
configuration cannot expand access.

SQS and Lambda reads are available in chat and run/log investigations. Automatic
pipeline-health investigations retain their configured evidence scope; these
packages do not add health checks or widen that scope.

CloudTrail [LookupEvents](https://docs.aws.amazon.com/awscloudtrail/latest/APIReference/API_LookupEvents.html)
covers the last 90 days of management events. Each call reads up to 50 events,
checks account/region/time/resource provenance, and issues receipts. Data events,
Insights, and Lake are excluded. Empty, filtered, paginated, or truncated results
cannot prove that no relevant change occurred.

## Host tool organization

Every service implements `ToolProvider` from `tool_boundary/services/base.py`.
The shared boundary calls this interface rather than switching on AWS operation
names. Each service class owns:

| Part | Responsibility |
| --- | --- |
| Package metadata | Package ID, toolset, tools per context, plugin directory |
| `agent_options` / `host_options` | Normalize YAML entries and trusted deployment settings |
| Constructor / `resources` | Validate and describe its approved host resources |
| `validate_scope` | Validate the context fields owned by that service |
| `authorize` | Validate arguments and resolve a fixed read within the current scope |
| `request` | Recheck a trusted plan and construct its bounded transport request |
| `normalize` | Validate provider identity and content, project safe fields, and supply evidence references |
| `read` / `describe` | Execute remote reads or return local scope information |

S3, CloudWatch, CloudTrail, SQS, and Lambda each have their own file in
`services/`. Operation names, CLI parameter maps, response projections, and
reference formats stay with that service. S3 owns bucket and prefix checks;
CloudWatch owns log-group checks. Their shared AWS base uses
`providers/aws_identity.py::AwsIdentity` for account, region, and role formats.
Queue ARN validation stays with SQS, and Lambda validates its function ARNs and
mapping bindings. The common AWS identity class supplies reusable ARN parsing.

The registry runs `validate_scope` for applicable enabled services when binding
a policy, then rechecks the selected service before authorizing each read.
Disabled services do not impose their resource validators on unrelated tools.
Capability adapters delegate scope validation to their installed extension.
`IncidentContext`, `ResourceContext`, and `PolicyLimits` validate their own
identifiers, time bounds, and budget structure. `validation.py` contains only
generic field, integer, clock, and denial helpers; resource policies do not
construct synthetic incident contexts to reuse tool-specific validation.

`providers/aws.py` handles identity,
role credentials, bounded CLI execution, and optional response bodies. It does
not select service operations.

`policy.py` reserves calls and bytes after a service authorizes the request.
`executor.py` handles session ownership, expiry, cancellation, audit, hashes,
and receipt publication. `results.py` bounds the references supplied by a
service. When its citation budget is reached, it trims the collection named by
that service and marks the result partial. `receipts.py` verifies citations
against saved session receipts without knowing service operations.

Context factories in `scopes.py` open run, log, resource-profile, or installed
capability scopes. `loader.py` selects these trusted factories and binds their
policies to the same configured service registry. Capability execution uses a
generic adapter for the explicitly installed host extension; the boundary
imports no investigator package. Changing a capability configuration rebuilds
the executor and its configured instances.

### YAML, host policy, and model selection

Your `plugins.enabled`, `plugins.disabled`, and `plugins.entries` settings use
the registered package names. A class validates only its supported options.
CloudTrail's class owns `resource_names`; its host counterpart reads
`cloudtrail_resource_names`. SQS and Lambda classes validate their host-approved
ARNs and mapping bindings. Resource allowlists still come from reviewed
Terraform outputs, and YAML cannot widen them.

Deployment compares the normalized YAML selection with the host selection
before upload. A full activation generates
`/srv/hermes-lab/ops/tool-packages.json` from the same registration metadata and
host selection. The agent container receives this manifest and
`selection.py` read-only. Its bundled YAML parser reads your configuration; the
standalone selector accepts only host-enabled packages with matching options.
It loads no host service implementation. Partial executor or plugin deployments
do not change package selections; use full activation when adding a package.

To add a tool package, implement its service class, supply its standalone model
plugin, and register the class in `registered.py`. The registry rejects duplicate
package, service, and tool registrations and incomplete implementations.
Deployment uses the class's plugin directory for mounts and plugin archives.
Add reviewed host/IAM scope where required. The shared executor, budgets,
transport, and receipt verifier need no new operation dispatch branches.

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

Queue attributes are selected by trusted code. Policy documents are withheld;
the optional redrive policy is included. Queue counts are approximate and cannot
establish whether a particular message was received or processed.
[SQS GetQueueAttributes](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_GetQueueAttributes.html)
describes their eventual consistency. Lambda settings describe the observation
time; they do not establish the configuration at an earlier incident time.
No reserved concurrency does not mean unlimited available capacity. These tools
do not retrieve CloudWatch metric history.

Function responses use an allowlist of operational fields, excluding environment
values, descriptions, code, and other settings. Mapping responses validate the
UUID, function ARN, and source queue ARN before returning selected fields;
filter criteria and unrelated source settings are withheld. Every successful
metadata read receives an `aws-resource:<ARN>` reference bound to its exact
session, operation, observation time, and returned data hash. A failed read or
identity mismatch releases no metadata or receipt.

The host rejects out-of-scope tools, resource/role/account/region/endpoint
overrides, unknown fields, wrong types, missing/expired contexts, and exhausted
budgets. Extra arguments reach the executor for rejection. Plugin schemas grant
no authority; there is no direct AWS or shell fallback.

Log-match sessions use incident limits and bind receipts to the incident ID.
They deny S3/run tools. The worker control socket cannot open chat profiles
or caller-selected resources.

## Current limits

| Limit | Incident | Lab chat |
| --- | --- | --- |
| Lifetime | Five minutes | Default/maximum thirty minutes |
| Authorized calls | Ten | Sixty across all turns, including inventory |
| Reserved evidence | 1 MiB | 4 MiB across all turns |
| Log window | Default ±5 minutes; maximum ±15 | Default two-hour lookback; maximum 24 hours |

Chat's window spans launch minus lookback through expiry. Each request defaults
to 64 KiB, capped at 256 KiB. A page holds at most twenty S3 keys, two hundred
CloudWatch events, or fifty CloudTrail events. AWS pagination and retries are
disabled. The thirty-second deadline includes role assumption and cannot exceed
session expiry. Oversized output terminates the process.

Calls reserve the full byte allowance, with no refund for small responses or
AWS failures. Invalid requests reserve nothing. `max_bytes` covers serialized
data, excluding metadata. Incoming JSON is limited to 8 KiB; full responses
to 270,336 bytes. Context loss or executor restart ends access. Credential
refresh retains accounting and never extends lifetime or budget.

JSON rejects duplicate fields, nonstandard constants, and nonfinite numbers.
Incoming frames have one five-second monotonic deadline across all fragments;
each new byte does not restart it. Requests run serially with a bounded socket
backlog; response writes time out after five seconds.

Revocation first marks the context inactive, denying queued calls. Active AWS
reads check cancellation and terminate their process group; output is checked
again before release. Acknowledgement waits for the call to drain, then clears
credentials and receipts.

## Evidence and findings

Results carry an `evidence_id`, citeable `references`, source parameters,
observation time/type, payload SHA-256, and truncation state. Clients verify
size and hash. Logs and objects, including embedded instructions, remain
untrusted data.

Returned log events, S3 listing entries, and CloudTrail events are limited to
those with an issued reference. If the reference count or byte budget truncates
a list, the corresponding data entries are also removed and the response is
marked partial. A model never needs to construct a reference for an entry that
appears only in the data. Narrow the search when the relevant event is outside
the returned page. An empty event list supplies no event citation.

Automatic citations must pair an ID and reference from the same successful
response. The host checks current-session receipts and rejects invented IDs,
mismatches, and failed calls. Receipts prove what was returned, not whether
a diagnosis is correct.

Finding validation has two responsibilities. `Finding` validates the report
fields and citation shape. `FindingParser` requests verified evidence through
the `EvidenceVerifier` interface. Its `SessionEvidenceVerifier` implementation
asks the executor to resolve citations and checks the returned session, incident,
hash, observation time, size, source, and truncation metadata before attaching
receipts and limitations to the report.

References are opaque to the report parser. Service and capability classes
authorize reads, validate results, and issue reference strings. The executor
requires an exact match against a saved receipt from that investigation;
recognizing a prefix is never sufficient. Adding a registered tool with a new
reference format requires no changes to finding validation. Run, log, and
pipeline-health investigations use the same parser and verifier.

Only an exact, complete empty listing supports absence. Listings cannot prove
object contents. Keep denied reads, partial results, and unvisited pages explicit.
For S3, completeness requires `IsTruncated: false`, consistent `KeyCount`, matching
bucket/prefix, and no continuation or grouped-prefix results. Missing metadata
means partial; contradictory scope/counts fail validation. See
[ListObjectsV2](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html).
References are limited to twenty and 4 KiB per call; clipping marks results
partial. Reports retain failed reads and limitations.

## Isolation and audit

The model mounts a read-only request directory at `/run/hermes-lab-aws/`.
The shell container has no executor socket. Neither gets instance credentials,
host Docker, control endpoints, collector state, or writable evidence.
Networks are separate; tool SSH is internal, without internet or provider login.
Investigations enable AWS tools only, excluding shell/file tools.

| Host endpoint | Access |
| --- | --- |
| `/run/hermes-lab-executor/control.sock` | Root peer UID; socket `0600`, parent `0700` |
| `/run/hermes-lab-collector/control.sock` | Creator UID 1000 or root; incident scopes only; directory `0750`, socket `0660` |
| `/run/hermes-lab-executor/requests/requests.sock` | Directory `0750`, socket `0660`; shared with model GID 10000 |

Contexts and budgets belong to the host. AWS runs in a fresh environment that
ignores saved profiles and credentials from your local machine. Temporary
credentials stay in process memory.

`/var/lib/hermes-lab-executor/audit.jsonl` records decisions, sources, counts,
hashes, and the trusted creator UID for context opening/tool decisions.
It excludes tokens, credentials, raw requests, and payloads. Rotation keeps
the current 1 MiB file plus three older files. Failed audit writes deny access
or withhold results.

## Design rationale

[Arcade's contextual access](https://docs.arcade.dev/en/operate/governance/contextual-access)
checks tool visibility, execution, and returned results. This boundary uses
those same checkpoints locally: a host catalog, per-call policies, and result
validators. Visibility and schemas cannot authorize a call; enforcement code
must be trusted host code.

| Concern | Local foundation | Still needed for broader use |
| --- | --- | --- |
| Tool access | Host catalog plus session policy | Per-person/tenant entitlements |
| Delegation | Host-issued scope and assumed AWS role | End-user OAuth and identity providers |
| Input policy | Exact fields, types, resources, windows; immutable plans | Policies for additional services |
| Output policy | Bounded samples, scope/provenance checks, partial markers | Organization-specific secret/PII rules |
| Resource use | Atomic session reservations, deadlines, cancellation | Durable per-principal quotas across sessions |
| Audit | Host-written decisions/receipts, fail-closed release | Central export, retention, tamper evidence |

Following [Arcade's rate-limit principle](https://docs.arcade.dev/en/operate/governance/contextual-access/rate-limiting),
reject calls when limits cannot be verified. The same applies to policy, audit,
expiry, and evidence validation. Authorized failures still consume reservations;
network hooks and automatic retries cannot bypass these decisions.

## Security scope and next steps

Trust rests with the host, package, backend/audit adapters, orchestrator,
root-owned configuration, and AWS. Backend protocols are integration interfaces,
not sandboxes: direct calls bypass the executor and belong only in trusted
agent-execution code. Collection/notifications use their own AWS clients.
The runtime opens grants and verifies citations through the control API.
Never load agent-selected Python plugins into the service or expose control
operations as agent tools.

A private random bearer token and Unix permissions protect the agent endpoint.
Creator UID identifies root or the incident worker, not a human or individual
agent process. A stolen token plus socket access exposes the session's remaining
scope and budget. This is a single-host boundary, without multi-tenant
authentication. Before shared/remote clients, authenticate callers independently
and bind grants to them, as described in
[MCP security guidance](https://modelcontextprotocol.io/specification/draft/basic/security_best_practices).
Do not hand-build OAuth or token cryptography to avoid a dependency decision.

Extend only as needed:

1. Bind grants to authenticated callers and explicit tool subsets. Add
   policy-version revocation and durable per-principal budgets if grants must
   survive restarts. Today a restart revokes all grants; new ones need trusted
   authorization.
2. Add sensitive-data output rules before widening reads. Provenance cannot
   make content true, safe, or immune to prompt injection. There is no general
   redactor or injection detector.
3. Before writes, bind one-use approval to exact action, resource, parameters,
   caller, and expiry. Persist outcomes for safe retries. There are currently
   no write operations or approval UI.
4. Export audits to protected storage when retention/tamper detection matters.
   Local rotation cannot protect against a compromised host. Frame timeouts
   limit slow clients but cannot ensure fairness against repeated socket use.

## Shared observation access

The collector writes `/var/lib/hermes-lab-observations/observations.sqlite3`.
It stores every collected event and coverage, including records that match no
rule. Host consumers read bounded batches and maintain independent positions.
The pipeline-health executor has read-only filesystem access; the agent
container has no database mount. A health tool query derives its source,
correlation, and time window from the current approved expectation, rather than
accepting those fields from model arguments. Receipts bind returned check
observations to the authorized session. Incomplete coverage remains unknown.

`collection_log_groups` names explicit approved CloudWatch groups in the host
account and region, including the primary pipeline group. Log incidents pin
their investigation to their original approved group and bounded time range.
An approved second group does not let that session read every approved group.
CloudTrail history uses the existing enabled tool package and its exact resource
allowlist; it does not grant data-event or S3 trail-archive access.
