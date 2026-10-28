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

resource "aws_iam_role_policy" "observation_evidence" {
  # Preserve the deployed policy name; evidence locations come from inputs.
  name = "read-synthetic-pipeline"
  role = aws_iam_role.hermes.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = local.evidence_bucket_arn
        Condition = { StringLike = { "s3:prefix" = ["input/*", "output/*", "runs/*"] } }
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = [for prefix in ["input", "output", "runs"] : "${local.evidence_bucket_arn}/${prefix}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["logs:FilterLogEvents"]
        Resource = "${local.evidence_log_group_arn}:*"
      }
    ]
  })
}
