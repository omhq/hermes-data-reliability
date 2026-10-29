# AWS access and tools

Agent tool calls pass through a host executor for bounded, read-only AWS access.
Chat and automatic investigations use the same boundary.

Collection runs separately. `collector/aws.py` validates account/roles, assumes
its read role as `hermes-lab-collector`, and reads the configured log group.
It has its own pagination, deadlines, and audit, using host deployment JSON.
It needs no agent grant, tool catalog, or enabled plugin. Both paths currently
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

## Tool packages

Each native Hermes plugin owns its registration, schemas, and Unix-socket client.
Each imports only its own files and the standard library; copy the whole folder
to reuse it.

| Package | Source | Toolset |
| --- | --- | --- |
| CloudWatch Logs | `investigator/tools/aws_cloudwatch/` | `aws_cloudwatch` |
| CloudTrail history | `investigator/tools/aws_cloudtrail/` | `aws_cloudtrail` |
| S3 and resource inventory | `investigator/tools/hermes_lab_aws/` | `hermes_lab_aws` |

`tool_boundary/catalog.py` selects packages and the host tool allowlist.
Plugins do not import it; authorization and AWS calls stay on the host.

Select packages in `investigator/runtime/config.yaml`:

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

S3 and CloudWatch are enabled by default. CloudTrail needs at least one exact
resource name. Packages work independently; `plugins.disabled` wins over
`plugins.enabled`. Unknown names and invalid selections are rejected.
An investigation needs at least one enabled package.

Use the [setup workflow](SETUP.md): review/apply the agent Terraform plan, then
deploy. Terraform turns this YAML into a trusted tool selection and optional
CloudTrail IAM grant. Chat and automation use the selected toolsets. The host
rejects disabled tools before reserving budget or calling AWS; model-writable
configuration cannot expand access.

CloudTrail [LookupEvents](https://docs.aws.amazon.com/awscloudtrail/latest/APIReference/API_LookupEvents.html)
covers the last 90 days of management events. Each call reads up to 50 events,
checks account/region/time/resource provenance, and issues receipts. Data events,
Insights, and Lake are excluded. Empty, filtered, paginated, or truncated results
cannot prove that no relevant change occurred.

## Tools and policies

| Scope | Tool | Result |
| --- | --- | --- |
| Resource | `list_aws_resources` | Configured targets and limits; no AWS discovery |
| Resource | `describe_cloudwatch_scope` | Approved log group/window; no AWS call |
| Resource | `describe_cloudtrail_scope` | Approved resource names/window; no AWS call |
| Resource | `search_log_events` | One approved group/window page |
| Resource | `list_s3_objects` | One page within an approved bucket prefix |
| Resource | `read_s3_object` | Text sample of an approved object |
| Incident | `search_run_logs` | Nearby events filtered by run ID |
| Incident | `list_run_objects` | Expected run objects or attempt directory |
| Incident | `read_run_object` | Input, output, or known attempt-log sample |
| Log match | `search_log_events` | Approved group within ±5 minutes of the event |
| Resource, incident, log match | `lookup_cloudtrail_events` | One approved resource's management-event page within the session window |

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

Automatic citations must pair an ID and reference from the same successful
response. The host checks current-session receipts and rejects invented IDs,
mismatches, and failed calls. Receipts prove what was returned, not whether
a diagnosis is correct.

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
