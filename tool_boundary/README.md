# Tool boundary

A trusted, bounded execution boundary for agent requests. This Python 3.14
package owns authorization, session budgets, read execution, output validation,
audit, and evidence receipts. Its current policies support the lab's S3,
CloudWatch Logs, and CloudTrail reads requested by the agent. Collection and
other trusted host operations use their own application clients; this package
is not a general AWS or subprocess utility for the application.

Every Python import is from the standard library or this package. You can copy
the complete `tool_boundary/` folder into another Python application's import
root. It does not import Hermes, `investigator`, a cloud SDK, or an agent
framework. The included AWS adapter invokes the existing `/usr/bin/aws` binary;
the socket service requires Linux and its peer-credential API.

## Entry points

- `executor.Executor(config, backend, audit)` accepts trusted configuration,
  a `contracts.ReadBackend`, and a `contracts.AuditSink`.
- `Executor.execute(envelope)` is the untrusted request entry point. It returns
  a bounded evidence result or a fixed error code.
- `register`, `revoke`, `status`, and `evidence` belong to trusted control code.
  Never expose them alongside the agent's tool endpoint.
- `models.ApprovedAction` is an immutable execution plan. It is not an
  independently verifiable capability or authorization to call a backend.
  `parameters_dict()` returns a detached provider payload.
- `server` supplies the Linux Unix-socket service; `aws.AwsBackend` and
  `audit.AuditLog` supply its concrete adapters. Policies, wire framing,
  subprocess bounds, result validation, and receipts have separate modules.

You must run this package outside agent-controlled processes. Python imports
and immutable records do not isolate hostile Python code. Keep configuration,
credentials, control sockets, audit files, and session state inaccessible to
the agent. A new service needs an explicit policy and output validator as well
as a backend; registering a tool name alone never grants access.

## Checks

Run on your workstation from the repository root:

```bash
.venv/bin/python -E -S -m tool_boundary.server --help
.venv/bin/python -m unittest discover -s tests/tool_boundary -t .
```

The tests include copying this package into an empty directory and importing
every module with isolated Python and site packages disabled.

See [access rules and design rationale](../docs/ACCESS.md),
[architecture](../docs/ARCHITECTURE.md), and [deployment](../docs/SETUP.md).
