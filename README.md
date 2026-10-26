# Hermes Data Reliability

An AWS lab for investigating failed data loads with Hermes.

```text
Scheduled producer Lambda -> S3 input/ -> SQS -> Processor Lambda -> S3 output/
```

A host collector applies YAML severity/match rules to existing CloudWatch logs.
Hermes investigates with scoped AWS reads and proposes fixes. Findings live in
`/srv/hermes-lab/findings/reports/` on the EC2..

## Use the lab

Start with the [operations guide](OPERATIONS.md) for SSH and current IP lookup.
Once connected to EC2:

```bash
sudo labctl status
sudo labctl pause
sudo labctl start
sudo labctl chat
```

## Guides

- Run: [setup](docs/SETUP.md), [operations](OPERATIONS.md),
  [recovery](docs/RECOVERY.md).
- Understand: [architecture](docs/ARCHITECTURE.md),
  [AWS access and tools](docs/ACCESS.md),
  [CloudWatch configuration](docs/OBSERVATION_CONFIG.md).
- Verify: [evaluations](docs/EVALUATION.md).
- Plan: [roadmap](ROADMAP.md).

## Development

Use Python 3.14. From the repository root:

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -t . -v
```

In VSCode, select this repository's `.venv`.
The shared Pyright configuration targets Python 3.14 on Linux, the deployment
platform. Keep Pylance current enough to support that version. The workspace
setting defaults new selections to `.venv`. The standalone plugin verifier
loads Hermes APIs at runtime inside the pinned container.
