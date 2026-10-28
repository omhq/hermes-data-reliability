locals {
  agent_configuration = yamldecode(file("${path.module}/../../investigator/runtime/config.yaml"))
  enabled_tool_packages = [
    for name in local.agent_configuration.plugins.enabled : name
    if !contains(try(local.agent_configuration.plugins.disabled, []), name)
  ]
  cloudtrail_resource_names = try(local.agent_configuration.plugins.entries.hermes-cloudtrail.resource_names, [])
  evidence_bucket_arn       = "arn:${data.aws_partition.current.partition}:s3:::${var.evidence_bucket_name}"
  evidence_log_group_arn    = "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${var.expected_account_id}:log-group:${var.evidence_log_group}"
}

resource "aws_iam_role" "investigator" {
  name                 = "${var.project_name}-investigator"
  description          = "Scoped pipeline evidence reads for the trusted host executor"
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "TrustLabHost"
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { AWS = aws_iam_role.hermes.arn }
    }]
  })
}

resource "aws_iam_role_policy" "investigator" {
  name = "read-pipeline-evidence"
  role = aws_iam_role.investigator.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Sid      = "ListEvidencePrefixes"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = local.evidence_bucket_arn
        Condition = {
          StringLike = {
            "s3:prefix" = ["input/*", "output/*", "runs/*"]
          }
        }
      },
      {
        Sid    = "ReadEvidenceObjects"
        Effect = "Allow"
        Action = "s3:GetObject"
        Resource = [
          for prefix in ["input", "output", "runs"] :
          "${local.evidence_bucket_arn}/${prefix}/*"
        ]
      },
      {
        Sid      = "ReadPipelineLogs"
        Effect   = "Allow"
        Action   = "logs:FilterLogEvents"
        Resource = "${local.evidence_log_group_arn}:*"
      }
      ], contains(local.enabled_tool_packages, "hermes-cloudtrail") ? [
      {
        Sid      = "ReadCloudTrailHistory"
        Effect   = "Allow"
        Action   = "cloudtrail:LookupEvents"
        Resource = "*"
        Condition = {
          StringEquals = { "aws:RequestedRegion" = var.aws_region }
        }
      }
    ] : [])
  })

  lifecycle {
    precondition {
      condition = alltrue([
        for name in local.enabled_tool_packages :
        contains(["hermes-lab-aws", "hermes-cloudwatch", "hermes-cloudtrail"], name)
      ])
      error_message = "Agent YAML contains an unsupported tool package."
    }
    precondition {
      condition     = !contains(local.enabled_tool_packages, "hermes-cloudtrail") || length(local.cloudtrail_resource_names) > 0
      error_message = "Enabling CloudTrail requires explicit resource_names in agent YAML."
    }
  }
}

resource "aws_iam_role_policy" "assume_investigator" {
  name = "assume-pipeline-investigator"
  role = aws_iam_role.hermes.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "AssumeInvestigatorOnly"
      Effect   = "Allow"
      Action   = "sts:AssumeRole"
      Resource = aws_iam_role.investigator.arn
    }]
  })
}

output "investigator" {
  description = "Nonsecret identifiers for operator permission verification."
  value = {
    role_arn                  = aws_iam_role.investigator.arn
    role_name                 = aws_iam_role.investigator.name
    host_role_arn             = aws_iam_role.hermes.arn
    account_id                = var.expected_account_id
    region                    = var.aws_region
    bucket                    = var.evidence_bucket_name
    log_group                 = var.evidence_log_group
    log_group_arn             = local.evidence_log_group_arn
    read_policy_name          = aws_iam_role_policy.investigator.name
    tool_packages             = local.enabled_tool_packages
    cloudtrail_resource_names = local.cloudtrail_resource_names
  }
}
