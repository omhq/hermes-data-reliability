# Hermes Data Reliability

Reproduce failed data loads in AWS and investigate them with Hermes Agent.
You configure log sources and match rules in YAML. A host collector detects
incidents, and Hermes follows the evidence with scoped AWS reads to explain
what failed and propose a fix.

```mermaid
flowchart TB
    subgraph pipeline["AWS · Example data pipeline"]
        direction LR
        producer["Scheduled<br/>Producer Lambda"] --> input[("S3<br/>input/")]
        input --> queue["SQS"]
        queue --> processor["Processor Lambda"]
        processor --> output[("S3<br/>output/")]
    end

    subgraph investigator["EC2 · Investigation and reporting"]
        direction LR
        collector["Host collector<br/>YAML match rules"]
        agent["Hermes Agent<br/>Scoped evidence reads"]
        reports["Reports<br/>Markdown + JSONL"]
        collector -->|Matched incident| agent
        agent -->|Host validates findings| reports
    end

    pipeline -.->|Existing logs| logs["CloudWatch Logs"]
    logs -->|Poll configured sources| investigator
    investigator -->|Agent prose + metadata| email["Email via SNS<br/>When configured"]

    classDef pipeline fill:#eff6ff,stroke:#2563eb,color:#1e3a8a
    classDef observe fill:#f1f5f9,stroke:#64748b,color:#0f172a
    classDef agent fill:#ecfdf5,stroke:#059669,color:#064e3b
    classDef report fill:#f5f3ff,stroke:#7c3aed,color:#4c1d95
    class producer,input,queue,processor,output pipeline
    class logs,collector observe
    class agent agent
    class reports,email report
    style pipeline fill:transparent,stroke:#94a3b8
    style investigator fill:transparent,stroke:#94a3b8
```

Reports include an explanation, uncertainty, evidence references, and a proposed
action. Read them on EC2 at `/srv/hermes-lab/findings/reports/`, or configure
`alert_email` for agent prose and incident metadata by email. CloudWatch health
monitoring remains active with its notification actions disabled.

## Quick start

For a new lab, follow [setup and deployment](docs/SETUP.md). For an existing
lab, use the [operations guide](docs/OPERATIONS.md) to find the current address
and connect from your workstation. Then run **on EC2**:

```bash
sudo labctl start
sudo labctl status
sudo labctl chat
```

Use `sudo labctl pause` to pause the investigator. Synthetic data generation
has a [separate control](docs/OPERATIONS.md#pause-or-resume-synthetic-data-generation).

## Guides

- [Architecture](docs/ARCHITECTURE.md) — data flow, investigation limits, and reporting.
- [Observation configuration](docs/OBSERVATION_CONFIG.md) — log sources, match rules, and prompts.
- [AWS access and tools](docs/ACCESS.md) — authorization and evidence boundaries.
- [Recovery](docs/RECOVERY.md) — backups, restore, and upgrades.
- [Evaluations](docs/EVALUATION.md) — reproducible scenarios and verification.

## Development

Use **Python 3.14**. Run on your workstation, from the repository root:

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

With the virtual environment active, run the checks from the repository root:

```bash
python -m ruff format --check .
python -m ruff check .
python -m unittest discover -s tests -t . -v
python -m compileall -q investigator pipeline evals tests
git diff --check
```

In VS Code, select this repository's `.venv` and use a Pylance/Pyright version
that supports Python 3.14. Type checking targets Linux, the deployment platform.
