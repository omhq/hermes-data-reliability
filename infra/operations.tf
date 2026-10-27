variable "alert_email" {
  description = "Optional agent report email destination; subscription requires email confirmation. Keep in ignored tfvars."
  type        = string
  sensitive   = true
  default     = null
}

resource "aws_sns_topic" "health" {
  name = "${var.project_name}-health"
}

resource "aws_sns_topic_subscription" "health_email" {
  count     = nonsensitive(var.alert_email != null) ? 1 : 0
  topic_arn = aws_sns_topic.health.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_sns_topic_policy" "health" {
  arn = aws_sns_topic.health.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Principal = { Service = "cloudwatch.amazonaws.com" }
      Action    = "SNS:Publish", Resource = aws_sns_topic.health.arn
      Condition = { StringEquals = { "aws:SourceAccount" = var.expected_account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "finding_notifications" {
  name = "publish-agent-findings"
  role = aws_iam_role.hermes.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sns:Publish"]
      Resource = aws_sns_topic.health.arn
    }]
  })
}

resource "aws_iam_role_policy" "health_metrics" {
  name = "publish-lab-health"
  role = aws_iam_role.hermes.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow", Action = ["cloudwatch:PutMetricData"], Resource = "*"
        Condition = { StringEquals = { "cloudwatch:namespace" = "HermesLab/PipelineLab" } }
      },
      {
        Effect   = "Allow", Action = ["backup:ListRecoveryPointsByBackupVault"]
        Resource = aws_backup_vault.state.arn
      }
    ]
  })
}

resource "aws_cloudwatch_metric_alarm" "host" {
  alarm_name          = "${var.project_name}-ec2-unhealthy"
  alarm_description   = "EC2 system or instance status checks failed."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  dimensions          = { InstanceId = aws_instance.hermes.id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "breaching"
  actions_enabled     = false
  alarm_actions       = [aws_sns_topic.health.arn]
  ok_actions          = [aws_sns_topic.health.arn]
}

resource "aws_cloudwatch_metric_alarm" "runtime" {
  alarm_name          = "${var.project_name}-runtime-unhealthy"
  alarm_description   = "Host health heartbeat is missing or reports an unhealthy runtime/poller/disk. Intentional pause is tracked."
  namespace           = "HermesLab/PipelineLab"
  metric_name         = "Healthy"
  dimensions          = { InstanceId = aws_instance.hermes.id }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 5
  datapoints_to_alarm = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  actions_enabled     = false
  alarm_actions       = [aws_sns_topic.health.arn]
  ok_actions          = [aws_sns_topic.health.arn]
}

resource "aws_kms_key" "backups" {
  description             = "Hermes Lab backup encryption and recovery-point copies"
  enable_key_rotation     = true
  deletion_window_in_days = 7
}

resource "aws_kms_alias" "backups" {
  name          = "alias/${var.project_name}-backups"
  target_key_id = aws_kms_key.backups.key_id
}

resource "aws_backup_vault" "state" {
  name = "${var.project_name}-state"
  # A customer-managed key supports encrypted EBS recovery-point copies.
  kms_key_arn   = aws_kms_key.backups.arn
  force_destroy = false
}

resource "aws_iam_role" "backup" {
  name = "${var.project_name}-backup"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "backup.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy_attachment" "backup" {
  role       = aws_iam_role.backup.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
}

resource "aws_iam_role" "restore" {
  name               = "${var.project_name}-restore"
  assume_role_policy = aws_iam_role.backup.assume_role_policy
}

resource "aws_iam_role_policy_attachment" "restore" {
  role       = aws_iam_role.restore.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForRestores"
}

resource "aws_backup_plan" "state" {
  name = "${var.project_name}-daily-state"
  rule {
    rule_name         = "daily-root-volume"
    target_vault_name = aws_backup_vault.state.name
    schedule          = "cron(0 2 * * ? *)"
    start_window      = 60
    completion_window = 180
    lifecycle {
      delete_after = 7
    }
  }
}

resource "aws_backup_selection" "state" {
  name         = "${var.project_name}-root-volume"
  plan_id      = aws_backup_plan.state.id
  iam_role_arn = aws_iam_role.backup.arn
  resources    = ["arn:${data.aws_partition.current.partition}:ec2:${var.aws_region}:${var.expected_account_id}:volume/${aws_instance.hermes.root_block_device[0].volume_id}"]
  depends_on   = [aws_iam_role_policy_attachment.backup]
}

resource "aws_cloudwatch_metric_alarm" "backups" {
  alarm_name          = "${var.project_name}-backup-failed"
  alarm_description   = "A lab EBS backup failed."
  namespace           = "AWS/Backup"
  metric_name         = "NumberOfBackupJobsFailed"
  dimensions          = { BackupVaultName = aws_backup_vault.state.name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  actions_enabled     = false
  alarm_actions       = [aws_sns_topic.health.arn]
}

output "operations" {
  value = {
    instance_id                   = aws_instance.hermes.id
    account_id                    = var.expected_account_id
    root_volume_id                = aws_instance.hermes.root_block_device[0].volume_id
    availability_zone             = aws_instance.hermes.availability_zone
    region                        = var.aws_region
    backup_vault                  = aws_backup_vault.state.name
    backup_role_arn               = aws_iam_role.backup.arn
    restore_role_arn              = aws_iam_role.restore.arn
    alert_topic_arn               = aws_sns_topic.health.arn
    notification_role_arn         = aws_iam_role.hermes.arn
    finding_notifications_enabled = nonsensitive(var.alert_email != null)
  }
}
