# Recovery and upgrades

Reboots happen. Saved work should survive them. On your local machine, run
commands from the repository root. Run `labctl` on the runtime host.

## Process interruption and reboot

Collection and investigation resume independently when `hermes-lab.target` is
enabled and `/srv/hermes-lab/ops/enabled` exists. `labctl pause` removes that intent,
so rebooting keeps paused work paused.

`findings/incidents.sqlite3` holds progress, pending work, definitions, findings,
and attempts. Collection saves each page's matches and progress together.
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
volume loss needs backups. Installation requires schema 2 and rejects older
formats without changing records. There is no automatic migration.

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
the installed definition only when present. Incidents restore with cursors,
continuation tokens, definition revisions, queue, and attempts. Older formats
need compatible code.

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

## Image upgrades and rollback

On the runtime host, choose a reviewed official digest:

```bash
sudo labctl upgrade --image nousresearch/hermes-agent@sha256:REVIEWED_DIGEST
sudo labctl rollback
```

Upgrade pauses, checkpoints, and waits for readiness; failure restores the prior
image. Rollback changes the image without replaying OAuth or reversing schema
changes. Incompatible state needs a checked file restore. Test an investigation
afterward.

`sudo labctl checkpoint` makes an offline local copy and restores your pause
choice. Keep checkpoints until an off-host recovery point is confirmed.

## Replace or rename a deployment

Use separate Terraform states and keep new generation disabled. Pause the old
generator/controller before transferring history/authentication over verified
SSH. Check S3/CloudWatch history, copy recovery points, and test the new
vault/key/role. Confirm SNS, an investigation, and a fresh backup before retiring
originals. Review replacements caused by resource renames or bootstrap edits.
