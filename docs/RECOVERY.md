# Recovery and upgrades

Workstation commands run from the repository root; `labctl` runs on EC2.

## Backups

AWS Backup snapshots encrypted root storage daily at **02:00 UTC**, retaining
seven days. Vault copies use a rotating KMS key and separate backup/restore
roles. Snapshots contain credentials, findings, sessions, and checkpoints;
protect them. Back up Terraform state separately.

On the workstation:

```bash
python3.14 -m investigator.runtime.recovery backup --profile YOUR_PROFILE
python3.14 -m investigator.runtime.recovery backup-status --profile YOUR_PROFILE \
  --job-id BACKUP_JOB_ID
python3.14 -m investigator.runtime.recovery list --profile YOUR_PROFILE
```

## Restore

The helper validates account/vault and creates an encrypted EBS volume in the
lab's availability zone:

```bash
python3.14 -m investigator.runtime.recovery restore-volume --profile YOUR_PROFILE \
  --recovery-point RECOVERY_POINT_ARN
python3.14 -m investigator.runtime.recovery restore-status --profile YOUR_PROFILE \
  --job-id RESTORE_JOB_ID
```

Attach as a secondary disk, identify by volume ID, and mount its root partition.
Use `ro,nouuid,norecovery` for a read-only XFS rehearsal; replay journals only on
the recovery volume. On the intended EC2 host:

```bash
sudo labctl pause
sudo labctl restore-files --source /mnt/recovered/srv/hermes-lab
sudo labctl start
sudo labctl status
```

Restore stages files, checks both SQLite databases, and checkpoints current
state. Current runtime/access policy remains authoritative. Saved YAML is used
when present; otherwise the installed definition stays. The database restore
includes saved reading positions, continuation tokens, definition revisions,
pending incidents, and attempts. Older checkpoints need code that supports
their database format.

Backups made after report email delivery is installed also contain
`findings/notifications.sqlite3`. Restore validates its format and integrity;
keep pending messages and delivery attempts with the findings. Collector reset
retains this database in place. Restoring an older backup can resend reports
accepted after that backup; it never redirects pending mail to a new topic.

Rotated OAuth may require `sudo labctl login`; use one controller per token
family. Before retiring the old host, verify database integrity, an investigation,
health, and backup completion. Remove recovery volumes afterward. Termination,
replacement, or destroy may delete the root disk.

For experiments, reset the collector to archive findings/queue and change the reading position while retaining budgets.
Local archives are not off-host backups.

## Image upgrades and rollback

On EC2, using a reviewed official digest:

```bash
sudo labctl upgrade --image nousresearch/hermes-agent@sha256:REVIEWED_DIGEST
sudo labctl rollback
```

Upgrade pauses, checkpoints, and waits for readiness; failure restores the prior
image selection. Rollback changes images without replaying OAuth or undoing
schema changes. Incompatible state needs a checked file restore. Test an
investigation afterward.

`sudo labctl checkpoint` makes a local offline copy and restores the pause
choice. Retain checkpoints until an off-host recovery point is confirmed.

## Replace or rename a deployment

Use separate Terraform states and keep new generation disabled. Pause the old
generator/controller before transferring history/authentication over verified SSH.
Verify S3/CloudWatch history, copy recovery points, and test the new vault/key/role.
Confirm SNS, an investigation, and a fresh backup before retiring originals.
Review replacements from resource renames or bootstrap edits.
