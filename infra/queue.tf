resource "aws_sqs_queue" "dead_letter" {
  name                      = "${var.project_name}-processor-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "input" {
  name                       = "${var.project_name}-input"
  visibility_timeout_seconds = 6 * local.processor_timeout
  message_retention_seconds  = 345600
  max_message_size           = 262144
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letter.arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "dead_letter" {
  queue_url = aws_sqs_queue.dead_letter.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.input.arn]
  })
}

resource "aws_sqs_queue_policy" "input" {
  queue_url = aws_sqs_queue.input.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowInputBucketNotifications"
        Effect    = "Allow"
        Principal = { Service = "s3.amazonaws.com" }
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.input.arn
        Condition = {
          ArnEquals    = { "aws:SourceArn" = aws_s3_bucket.evidence.arn }
          StringEquals = { "aws:SourceAccount" = var.expected_account_id }
        }
      },
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "sqs:*"
        Resource  = aws_sqs_queue.input.arn
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      }
    ]
  })
}

resource "aws_s3_bucket_notification" "input" {
  bucket = aws_s3_bucket.evidence.id
  queue {
    id            = "input-files-to-processor"
    queue_arn     = aws_sqs_queue.input.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "input/"
    filter_suffix = ".json"
  }
  depends_on = [aws_sqs_queue_policy.input]
}
