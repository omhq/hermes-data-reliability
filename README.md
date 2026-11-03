# Hermes Data Reliability

![Hermes overview: pipeline signals, independent investigation, findings, chat, and configured actions](docs/assets/hermes-architecture.svg)

Reproduce failed data loads in AWS and investigate them with Hermes Agent.
You configure log sources, match rules, and delivery expectations in YAML.
One collector saves shared evidence. Independent consumers apply log rules
and scheduled expectations, then save incidents for Hermes to investigate.
Hermes follows the evidence with scoped read-only tools, explains what it can
establish, and proposes a next step for you to review.

## Quick start

For a local runtime, follow [local Docker setup](docs/SETUP.md#local-docker-runtime),
create `.local/runtime.yaml` from `runtime.example.yaml` with your approved
resources, then run on your local machine. You need Docker with Compose v2,
Make, and AWS CLI v2; local Python and `.venv` are optional:

```bash
make configure AWS_PROFILE=YOUR_PROFILE
make up
```

`make up` stays in the foreground and streams startup and service logs. After
startup completes, run `make login` and `make chat` in another terminal from
the repository root. Press Ctrl+C in the first terminal to stop the local
runtime host and its nested containers.

The container runs the same installer and services as EC2. It reads your
approved AWS resources using your selected profile; Terraform is optional.
Saved state stays in local Docker volumes.

For a new lab, follow [setup and deployment](docs/SETUP.md). For an existing
lab, use the [operations guide](docs/OPERATIONS.md) to find the current address
and connect from your local machine. Then run **on the runtime host**:

```bash
sudo labctl start
sudo labctl status
sudo labctl chat
```

Use `sudo labctl pause` to pause the investigator. Synthetic data generation
has a [separate control](docs/OPERATIONS.md#pause-or-resume-synthetic-data-generation).

## Guides

- [Architecture](docs/ARCHITECTURE.md) — component responsibilities, data flow, investigation limits, and reporting.
- [Observation configuration](docs/OBSERVATION_CONFIG.md) — log sources, match rules, and prompts.
- [AWS access and tools](docs/ACCESS.md) — authorization and evidence boundaries.
- [Recovery](docs/RECOVERY.md) — backups, restore, and runtime image changes.
- [Evaluations](docs/EVALUATION.md) — reproducible scenarios and verification.

## Development

Run from the repository root on your local machine. Docker supplies Python
3.14 and all project dependencies; no local Python environment is required:

```bash
make check
make test
make format
```

`make check` validates formatting, lint, and Python compilation. `make test`
runs the test suite. Both use disposable containers without AWS credentials,
network access, or runtime volumes. `make format` uses a disposable container
to format and fix lint in your checkout. Review its changes with `git diff`.

For an ad hoc command with the same dependencies, use
`make dev ARGS="python --version"` or `make dev ARGS=bash`. This rebuilds the
source snapshot and runs in the same isolated environment as tests.

For VS Code completion and type checking, you can optionally install Python
3.14 and create a local environment with all project and development dependencies:

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Open the repository root in VS Code and select `.venv/bin/python` with
**Python: Select Interpreter**. The source packages are available from the
workspace; `requirements.txt` installs their external dependencies and test
tools. Use a Pylance/Pyright version that supports Python 3.14. Type checking
targets Linux, the deployment platform. Make commands do not use this environment.
