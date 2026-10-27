data "aws_partition" "current" {}

resource "aws_iam_role" "hermes" {
  name = "${var.project_name}-observer"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_instance_profile" "hermes" {
  name = "${var.project_name}-observer"
  role = aws_iam_role.hermes.name
}

resource "aws_iam_role_policy" "s3_evidence" {
  count = var.evidence_bucket_name == null ? 0 : 1
  name  = "read-lab-evidence"
  role  = aws_iam_role.hermes.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::${var.evidence_bucket_name}"
        Condition = {
          StringLike = { "s3:prefix" = [for prefix in var.evidence_prefixes : "${prefix}*"] }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = [for prefix in var.evidence_prefixes : "arn:${data.aws_partition.current.partition}:s3:::${var.evidence_bucket_name}/${prefix}*"]
      }
    ]
  })
}

resource "aws_iam_role_policy" "log_evidence" {
  count = length(var.evidence_log_groups) == 0 ? 0 : 1
  name  = "read-lab-logs"
  role  = aws_iam_role.hermes.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["logs:FilterLogEvents"]
      Resource = [for name in var.evidence_log_groups :
        "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${var.expected_account_id}:log-group:${name}:*"
      ]
    }]
  })
}
