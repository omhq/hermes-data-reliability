# Collector

Collect configured CloudWatch log sources, match bounded rules, and commit
incidents and page progress together. This package runs independently of the
agent. It uses `incidents` for durable storage, `host_aws` for trusted AWS CLI
execution, and the pinned PyYAML parser. It does not import `investigator` or
`tool_boundary`.

On your workstation, from the repository root with Python 3.14:

```bash
python -m pip install -r collector/requirements.txt
python -m collector.poll --help
python -m collector.preview --help
```

Provide an observation YAML and an approved host configuration containing
`account_id`, `region`, `log_group`, `host_role_arn`, and `role_arn`. The host
needs the AWS CLI and its configured identity. Run on that host from the release
root:

```bash
python -m collector.poll \
  --config /path/to/approved.json --observations /path/to/observations.yaml \
  --findings /path/to/private-findings
```

`collector.configure` validates and installs definitions. Initial configuration
requires an explicit `--template` or `--source`; the hosting application owns
that input. Later invocations can validate the installed definition directly.

Collection stores pending incidents and progress in `incidents.sqlite3`, writes
its own audit, and publishes `last-poll.json`. Investigation and report export
run separately. See [observation configuration](../docs/OBSERVATION_CONFIG.md)
and [architecture](../docs/ARCHITECTURE.md) for formats and bounds.
