# Recovery

Run commands from your local machine at the repository root unless marked for
the runtime host. Normal restarts preserve saved state; deployment does not
convert old formats.

## Local container saved state

On your local machine, `make stop` pauses writers and `make down` stops the
runtime host without deleting any volumes. The Compose project keeps separate
volumes for `/srv/hermes-lab`, collected observations, executor state, and nested
Docker images. `make up` rebuilds the source image and reuses those volumes.

Use `labctl checkpoint` inside `make shell` for a consistent checkpoint of
Hermes, findings, and collected observations. Preserve executor history
separately while its service is stopped, as described below. Copy backups off
Docker's storage before removing volumes. AWS Backup protects the EC2 host only.
Deleting local volumes discards authentication and investigation budget history.

Local AWS credentials remain in ignored `.local/docker/` and are copied into
the host container's temporary `/run/hermes-lab-credentials/aws.json` file.
Refresh them with `make credentials AWS_PROFILE=YOUR_PROFILE` after expiry.
Recreating a container
does not renew credentials or authorization. Refresh accepts only the configured
account and principal. Use `make configure` followed by `make up` for an
intentional principal or resource policy change; saved budget history remains.

## Process interruption and reboot

An enabled `hermes-lab.target` and `/srv/hermes-lab/ops/enabled` resume work after
reboot. `labctl pause` clears that intent. Collection and consumers resume from
their own committed positions. Interrupted investigation attempts stay charged;
a new authorized session can retry after the ten-minute claim lease expires.

| Saved state | Location |
| --- | --- |
| Queue, definitions, findings, attempts | `/srv/hermes-lab/findings/incidents.sqlite3` |
| Collected events, coverage, positions | `/var/lib/hermes-lab-observations/observations.sqlite3` |
| Pipeline-health history and outbox | `/var/lib/hermes-lab-executor/pipeline-health.sqlite3` |
| Chat/authentication | `/srv/hermes-lab/hermes/` |
| Report delivery | `/srv/hermes-lab/findings/notifications.sqlite3` |

Keep SQLite journals with their databases. Pause writers and use checkpoints
for consistent offline copies; local checkpoints do not protect against volume
loss. Saved observations never restore expired access.

## Backups

AWS Backup snapshots encrypted root storage daily at **02:00 UTC**, retaining
seven days. Snapshots include credentials and findings; protect access and back
up Terraform state separately. On your local machine:

```bash
python3.14 -m investigator.runtime.recovery backup --profile YOUR_PROFILE
python3.14 -m investigator.runtime.recovery backup-status --profile YOUR_PROFILE \
  --job-id BACKUP_JOB_ID
python3.14 -m investigator.runtime.recovery list --profile YOUR_PROFILE
```

## Restore

On your local machine, create a recovery volume in the lab's availability zone:

```bash
python3.14 -m investigator.runtime.recovery restore-volume --profile YOUR_PROFILE \
  --recovery-point RECOVERY_POINT_ARN
python3.14 -m investigator.runtime.recovery restore-status --profile YOUR_PROFILE \
  --job-id RESTORE_JOB_ID
```

Attach the volume, identify it by volume ID, and mount its root partition.
Use `ro,nouuid,norecovery` for a read-only XFS rehearsal; replay journals only on
the recovery volume. On the intended runtime host:

```bash
sudo labctl pause
sudo labctl restore-files --source /mnt/recovered/srv/hermes-lab
sudo labctl start
sudo labctl status
```

`restore-files` checks databases and checkpoints current state before restoring
Hermes, incidents, shared observations, and registered operator YAML. Current
runtime/access policy stays authoritative; older formats are rejected.

Pipeline-health history needs a separate copy from the recovered volume while
the executor and pipeline-health service are stopped. Keep owner `hermes-lab-executor`
and private permissions, then recompile the intended expectation YAML before
restarting. Without this history, earlier observations remain unavailable.

Restore report-delivery attempts with findings. Older backups may resend reports
accepted afterward; pending messages never redirect to a changed topic. Rotated
OAuth may need `sudo labctl login`; use one controller per token family.

Before retiring the old host, verify database integrity, an investigation, health,
and backup completion. Remove recovery volumes afterward. Replacement or destroy
may delete the root disk. For a fresh reading position while keeping budgets,
use [collector reset](OPERATIONS.md#restart-collection-from-a-new-point).

## Breaking format changes

Pause the lab, finish chat, and stop the executor before discarding affected
state. Remove databases with their journals only after writers stop. Treat
incident, observation, pipeline-health, and notification references as related;
removing incident attempt history resets investigation budgets.

Recreate affected YAML in the current format and [deploy/activate](SETUP.md#deploy-the-runtime).
Owning packages initialize empty databases. Keep OAuth, credentials, SSH keys,
and infrastructure state outside this reset.

## Image upgrades and rollback

On the runtime host, choose a reviewed official digest:

```bash
sudo labctl upgrade --image nousresearch/hermes-agent@sha256:REVIEWED_DIGEST
sudo labctl rollback
```

Upgrade pauses, checkpoints, and waits for readiness, restoring the previous
image on failure. Rollback changes the image without replaying OAuth or
converting saved data. The image must support the current state format; test
an investigation afterward.

`sudo labctl checkpoint` makes an offline local copy and restores your pause
choice. Keep it until an off-host recovery point is confirmed.

## Replace or rename a deployment

Use separate Terraform states and keep new generation disabled. Pause the old
generator/controller before transferring history/authentication over verified
SSH. Confirm source history, SNS, an investigation, and a fresh backup before
retiring originals. Review replacements caused by renames or bootstrap edits.
