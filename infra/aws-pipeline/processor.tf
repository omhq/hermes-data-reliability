locals {
  processor_timeout = 30
}

resource "aws_cloudwatch_log_group" "processor" {
  name              = "/aws/lambda/${var.project_name}-loader"
  retention_in_days = 7
}

resource "aws_iam_role" "processor" {
  name = "${var.project_name}-loader"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "processor" {
  name = "write-synthetic-evidence"
  role = aws_iam_role.processor.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.evidence.arn}/input/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = [for prefix in ["output", "runs"] : "${aws_s3_bucket.evidence.arn}/${prefix}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.input.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.processor.arn}:*"
      }
    ]
  })
}

data "archive_file" "processor" {
  type        = "zip"
  output_path = "${path.module}/.terraform/processor.zip"

  dynamic "source" {
    for_each = toset(["__init__.py", "logging_config.py"])
    content {
      filename = "pipeline/${source.value}"
      content  = file("${path.module}/../../pipeline/${source.value}")
    }
  }

  dynamic "source" {
    for_each = toset([
      "__init__.py",
      "events.py",
      "handler.py",
      "logs.py",
      "processing.py",
    ])
    content {
      filename = "processor/${source.value}"
      content  = file("${path.module}/../../pipeline/processor/${source.value}")
    }
  }
}

resource "aws_lambda_function" "processor" {
  function_name    = "${var.project_name}-loader"
  role             = aws_iam_role.processor.arn
  runtime          = "python3.14"
  handler          = "processor.handler.handler"
  filename         = data.archive_file.processor.output_path
  source_code_hash = data.archive_file.processor.output_base64sha256
  timeout          = local.processor_timeout
  memory_size      = 128
  environment {
    variables = {
      EVIDENCE_BUCKET     = aws_s3_bucket.evidence.id
      PROCESSOR_QUEUE_ARN = aws_sqs_queue.input.arn
    }
  }
  depends_on = [aws_iam_role_policy.processor, aws_cloudwatch_log_group.processor]
}

resource "aws_lambda_event_source_mapping" "processor" {
  event_source_arn        = aws_sqs_queue.input.arn
  function_name           = aws_lambda_function.processor.arn
  batch_size              = 5
  enabled                 = true
  function_response_types = ["ReportBatchItemFailures"]
}

