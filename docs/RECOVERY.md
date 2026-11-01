# Recovery

Reboots happen. Saved work should survive them. On your local machine, run
commands from the repository root. Run `labctl` on the runtime host.

## Process interruption and reboot

Collection and investigation resume independently when `hermes-lab.target` is
enabled and `/srv/hermes-lab/ops/enabled` exists. `labctl pause` removes that intent,
so rebooting keeps paused work paused.

`findings/incidents.sqlite3` holds pending work, definitions, findings, and
attempts. `/var/lib/hermes-lab-observations/observations.sqlite3` holds original
events, collection coverage, ingestion cursors, and consumer positions. Collection
commits each provider page and its progress together. Consumers commit incidents
before acknowledging their event batch, so interruption can safely replay.
Investigation saves ownership and attempt charges before Hermes starts, with
no database lock during model/tool calls. Restarting keeps this state intact.

Interrupted attempts stay charged. Their ten-minute lease must expire before
another automatic attempt, even after reboot. The model worker and authorization
expire sooner. Recovery creates a fresh session under current host authorization.
Three attempts per incident and rolling hour still apply; a third interruption
exhausts the incident. Expired attempts cannot overwrite newer results.
Committed findings export on a later tick without another model call.

Keep SQLite journals beside the database on persistent local storage; never
delete them after a crash. For offline copies, pause both workers and use a
checkpoint. Durability covers committed state when storage honors synchronization;
volume loss needs backups. Database readers accept only the current format or
an empty database. Deployment does not convert saved state.

Pipeline-health observations and the incident outbox survive executor restarts
in `/var/lib/hermes-lab-executor/pipeline-health.sqlite3`. Root-volume snapshots
include this database and `/etc/hermes-lab/pipeline-health.json`. Its source YAML
is `/srv/hermes-lab/pipeline-health.yaml`. Saved observations do not restore
expired access; investigations revalidate current definitions and authorization.
The seven-day observation retention and 24-hour automatic checking window still
apply after a restart.

The `restore-files` command below restores the Hermes, incident, and shared
observation databases. Checkpoints include every registered operator YAML
definition and a consistent copy of shared observations. Root-volume recovery reads shared
observations from its recovered `/var/lib/hermes-lab-observations/` directory.
For pipeline-health history, also restore its database from the recovered
volume while the executor and pipeline-health service are stopped. Keep its
owner as `hermes-lab-executor` and permissions private. Recompile the intended
expectation YAML against the current installation before restarting. Without
that history, the agent can inspect the current state but must report the old
observation as unavailable. Never copy live SQLite files without their journal.

## Backups

AWS Backup snapshots encrypted root storage daily at **02:00 UTC**, keeping
seven days. Vault copies use a rotating KMS key and separate backup/restore roles.
Protect snapshots: they contain credentials, findings, sessions, and checkpoints.
Back up Terraform state separately.

On your local machine:

```bash
python3.14 -m investigator.runtime.recovery backup --profile YOUR_PROFILE
python3.14 -m investigator.runtime.recovery backup-status --profile YOUR_PROFILE \
  --job-id BACKUP_JOB_ID
python3.14 -m investigator.runtime.recovery list --profile YOUR_PROFILE
```

## Restore

From your local machine, validate account/vault and create an encrypted EBS volume
in the lab's availability zone:

```bash
python3.14 -m investigator.runtime.recovery restore-volume --profile YOUR_PROFILE \
  --recovery-point RECOVERY_POINT_ARN
python3.14 -m investigator.runtime.recovery restore-status --profile YOUR_PROFILE \
  --job-id RESTORE_JOB_ID
```

Attach it as a secondary disk, identify it by volume ID, and mount its root
partition. Use `ro,nouuid,norecovery` for a read-only XFS rehearsal. Replay
journals only on the recovery volume. On the intended runtime host:

```bash
sudo labctl pause
sudo labctl restore-files --source /mnt/recovered/srv/hermes-lab
sudo labctl start
sudo labctl status
```

Restore stages files, checks both SQLite databases, and checkpoints current
state. Current runtime/access policy remains authoritative. Saved YAML replaces
the installed definition only when present. The observation database restores source and consumer positions, coverage, and
continuation tokens. Incidents retain definition revisions, queue, and attempts. Older formats
are rejected by current readers.

Backups taken after email setup also contain `findings/notifications.sqlite3`.
Restore validates its format/integrity. Keep pending messages and delivery
attempts with findings; collector reset leaves this database in place.
Older backups may resend reports accepted afterward, but never redirect
pending mail to a changed topic.

Rotated OAuth may need `sudo labctl login`; use one controller per token family.
Before retiring the old host, verify database integrity, an investigation,
health, and backup completion. Remove recovery volumes afterward. Termination,
replacement, or destroy may delete the root disk.

For a fresh experiment, [reset the collector](OPERATIONS.md#restart-collection-from-a-new-point)
to archive findings/queue and change the reading position while keeping budgets.
Local archives are not off-host backups.

## Breaking format changes

This project supports the current data and configuration formats only. For a
breaking change, recreate the affected lab state instead of converting it.

On the runtime host, pause the lab and stop the executor before removing an
affected database. Finish any active chat first. Remove a discarded database
together with its journal files only after its writers have stopped. Treat
incident, observation, pipeline-health, and notification databases as related
state when their identifiers or references change. Removing incident attempt
history resets its investigation budgets.

Recreate affected operator YAML using the current format, then deploy and
activate the current release using [Setup](SETUP.md#deploy-the-runtime). Empty
databases are initialized by their owning packages. Keep credentials, OAuth
state, SSH keys, and infrastructure state outside this reset. Normal restarts
and deployments with unchanged formats retain saved work.

## Image upgrades and rollback

On the runtime host, choose a reviewed official digest:

```bash
sudo labctl upgrade --image nousresearch/hermes-agent@sha256:REVIEWED_DIGEST
sudo labctl rollback
```

Upgrade pauses, checkpoints, and waits for readiness; failure restores the prior
image. Rollback changes the image without replaying OAuth or converting saved
data. The selected image must use the current state format. Test an
investigation afterward.

`sudo labctl checkpoint` makes an offline local copy and restores your pause
choice. Keep checkpoints until an off-host recovery point is confirmed.

## Replace or rename a deployment

Use separate Terraform states and keep new generation disabled. Pause the old
generator/controller before transferring history/authentication over verified
SSH. Check S3/CloudWatch history, copy recovery points, and test the new
vault/key/role. Confirm SNS, an investigation, and a fresh backup before retiring
originals. Review replacements caused by resource renames or bootstrap edits.
