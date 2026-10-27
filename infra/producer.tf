resource "aws_cloudwatch_log_group" "producer" {
  name              = "/aws/lambda/${var.project_name}-producer"
  retention_in_days = 7
}

resource "aws_iam_role" "producer" {
  name = "${var.project_name}-producer"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "producer" {
  name = "write-synthetic-input"
  role = aws_iam_role.producer.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.evidence.arn}/input/*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.producer.arn}:*"
      }
    ]
  })
}

data "archive_file" "producer" {
  type        = "zip"
  output_path = "${path.module}/.terraform/producer.zip"

  dynamic "source" {
    for_each = toset(["__init__.py", "logging_config.py"])
    content {
      filename = "pipeline/${source.value}"
      content  = file("${path.module}/../pipeline/${source.value}")
    }
  }

  dynamic "source" {
    for_each = toset(["__init__.py", "handler.py"])
    content {
      filename = "producer/${source.value}"
      content  = file("${path.module}/../pipeline/producer/${source.value}")
    }
  }
}

resource "aws_lambda_function" "producer" {
  function_name    = "${var.project_name}-producer"
  role             = aws_iam_role.producer.arn
  runtime          = "python3.14"
  handler          = "producer.handler.handler"
  filename         = data.archive_file.producer.output_path
  source_code_hash = data.archive_file.producer.output_base64sha256
  timeout          = 20
  memory_size      = 128
  environment {
    variables = {
      EVIDENCE_BUCKET     = aws_s3_bucket.evidence.id
      FAILURE_PROBABILITY = tostring(var.failure_probability)
    }
  }
  depends_on = [aws_iam_role_policy.producer, aws_cloudwatch_log_group.producer]
}

resource "aws_lambda_function_event_invoke_config" "producer" {
  function_name                = aws_lambda_function.producer.function_name
  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 120
}

resource "aws_iam_role" "scheduler" {
  name = "${var.project_name}-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "scheduler.amazonaws.com" }
      Condition = {
        StringEquals = { "aws:SourceAccount" = var.expected_account_id }
        ArnEquals    = { "aws:SourceArn" = "arn:${data.aws_partition.current.partition}:scheduler:${var.aws_region}:${var.expected_account_id}:schedule-group/default" }
      }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  # Keep the deployed policy name while narrowing its target to the producer.
  name = "invoke-lab-loader"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = aws_lambda_function.producer.arn
    }]
  })
}

resource "aws_scheduler_schedule" "producer" {
  # The schedule's existing AWS name remains valid for operator commands.
  name                = "${var.project_name}-loader"
  schedule_expression = "rate(1 minute)"
  state               = var.pipeline_schedule_enabled ? "ENABLED" : "DISABLED"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_lambda_function.producer.arn
    role_arn = aws_iam_role.scheduler.arn
    # Preserve literal Scheduler tokens; jsonencode escapes the brackets.
    input = "{\"scheduled_at\":\"<aws.scheduler.scheduled-time>\"}"
    retry_policy {
      maximum_event_age_in_seconds = 60
      maximum_retry_attempts       = 0
    }
  }
  depends_on = [
    aws_iam_role_policy.scheduler,
    aws_lambda_function_event_invoke_config.producer,
    aws_lambda_event_source_mapping.processor,
    aws_s3_bucket_notification.input,
  ]
}
