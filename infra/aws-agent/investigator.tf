locals {
  agent_configuration = yamldecode(file("${path.module}/../../investigator/runtime/config.yaml"))
  enabled_tool_packages = [
    for name in local.agent_configuration.plugins.enabled : name
    if !contains(try(local.agent_configuration.plugins.disabled, []), name)
  ]
  cloudtrail_resource_names = try(local.agent_configuration.plugins.entries.hermes-cloudtrail.resource_names, [])
  evidence_bucket_arn       = "arn:${data.aws_partition.current.partition}:s3:::${var.evidence_bucket_name}"
  evidence_log_group_arn    = "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${var.expected_account_id}:log-group:${var.evidence_log_group}"
  collection_log_groups     = distinct(concat([var.evidence_log_group], var.collection_log_groups))
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
        Resource = [for group in local.collection_log_groups : "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${var.expected_account_id}:log-group:${group}:*"]
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
      ] : [], contains(local.enabled_tool_packages, "hermes-sqs") ? [
      {
        Sid      = "ReadApprovedQueueAttributes"
        Effect   = "Allow"
        Action   = "sqs:GetQueueAttributes"
        Resource = var.sqs_queue_arns
      }
      ] : [], contains(local.enabled_tool_packages, "hermes-lambda") ? [
      {
        Sid      = "ReadApprovedFunctionSettings"
        Effect   = "Allow"
        Action   = ["lambda:GetFunctionConfiguration", "lambda:GetFunctionConcurrency"]
        Resource = var.lambda_function_arns
      }
      ] : [], contains(local.enabled_tool_packages, "hermes-lambda") && length(var.lambda_event_source_mappings) > 0 ? [
      {
        Sid      = "ReadApprovedEventSourceMappings"
        Effect   = "Allow"
        Action   = "lambda:GetEventSourceMapping"
        Resource = [for identifier in keys(var.lambda_event_source_mappings) : "arn:${data.aws_partition.current.partition}:lambda:${var.aws_region}:${var.expected_account_id}:event-source-mapping:${identifier}"]
      }
    ] : [])
  })

  lifecycle {
    precondition {
      condition = alltrue([
        for name in local.enabled_tool_packages :
        contains(["hermes-lab-aws", "hermes-cloudwatch", "hermes-cloudtrail", "hermes-pipeline-health", "hermes-sqs", "hermes-lambda"], name)
      ])
      error_message = "Agent YAML contains an unsupported tool package."
    }
    precondition {
      condition     = !contains(local.enabled_tool_packages, "hermes-cloudtrail") || length(local.cloudtrail_resource_names) > 0
      error_message = "Enabling CloudTrail requires explicit resource_names in agent YAML."
    }
    precondition {
      condition     = !contains(local.enabled_tool_packages, "hermes-sqs") || length(var.sqs_queue_arns) > 0
      error_message = "Enabling SQS requires explicit sqs_queue_arns."
    }
    precondition {
      condition     = !contains(local.enabled_tool_packages, "hermes-lambda") || length(var.lambda_function_arns) > 0
      error_message = "Enabling Lambda requires explicit lambda_function_arns."
    }
    precondition {
      condition = length(var.sqs_queue_arns) <= 50 && length(distinct(var.sqs_queue_arns)) == length(var.sqs_queue_arns) && alltrue([
        for arn in var.sqs_queue_arns : can(regex("^arn:${data.aws_partition.current.partition}:sqs:${var.aws_region}:${var.expected_account_id}:[A-Za-z0-9_-]{1,80}(\\.fifo)?$", arn))
      ])
      error_message = "Queue ARNs must be unique, literal, and in the approved account and region."
    }
    precondition {
      condition = length(var.lambda_function_arns) <= 50 && length(distinct(var.lambda_function_arns)) == length(var.lambda_function_arns) && alltrue([
        for arn in var.lambda_function_arns : can(regex("^arn:${data.aws_partition.current.partition}:lambda:${var.aws_region}:${var.expected_account_id}:function:[A-Za-z0-9_-]{1,64}$", arn))
      ])
      error_message = "Function ARNs must be unique, unqualified, and in the approved account and region."
    }
    precondition {
      condition = length(var.lambda_event_source_mappings) <= 50 && alltrue([
        for identifier, binding in var.lambda_event_source_mappings :
        can(regex("^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$", identifier)) &&
        contains(var.lambda_function_arns, binding.function_arn) && contains(var.sqs_queue_arns, binding.queue_arn)
      ])
      error_message = "Each mapping UUID must bind an approved function and queue."
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
    authentication = {
      source          = "instance"
      principal_arn   = aws_iam_role.hermes.arn
      assume_role_arn = aws_iam_role.investigator.arn
    }
    role_arn                     = aws_iam_role.investigator.arn
    role_name                    = aws_iam_role.investigator.name
    host_role_arn                = aws_iam_role.hermes.arn
    account_id                   = var.expected_account_id
    region                       = var.aws_region
    bucket                       = var.evidence_bucket_name
    log_group                    = var.evidence_log_group
    log_group_arn                = local.evidence_log_group_arn
    collection_log_groups        = local.collection_log_groups
    read_policy_name             = aws_iam_role_policy.investigator.name
    tool_packages                = local.enabled_tool_packages
    cloudtrail_resource_names    = local.cloudtrail_resource_names
    sqs_queue_arns               = var.sqs_queue_arns
    lambda_function_arns         = var.lambda_function_arns
    lambda_event_source_mappings = var.lambda_event_source_mappings
    health_resources = {
      for name, prefix in { pipeline_input = "input/", pipeline_output = "output/" } : name => {
        adapter                            = "s3_object"
        bucket                             = var.evidence_bucket_name
        region                             = var.aws_region
        prefix                             = prefix
      }
    }
  }
}
