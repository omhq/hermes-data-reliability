output "pipeline" {
  value = {
    bucket                  = aws_s3_bucket.evidence.id
    log_group               = aws_cloudwatch_log_group.processor.name
    function                = aws_lambda_function.processor.function_name
    function_arn            = aws_lambda_function.processor.arn
    producer_function       = aws_lambda_function.producer.function_name
    producer_function_arn   = aws_lambda_function.producer.arn
    producer_log_group      = aws_cloudwatch_log_group.producer.name
    queue_url               = aws_sqs_queue.input.id
    queue_arn               = aws_sqs_queue.input.arn
    dead_letter_url         = aws_sqs_queue.dead_letter.id
    dead_letter_arn         = aws_sqs_queue.dead_letter.arn
    event_source_mapping_id = aws_lambda_event_source_mapping.processor.uuid
    region                  = var.aws_region
    schedule_enabled        = var.pipeline_schedule_enabled
    schedule_name           = aws_scheduler_schedule.producer.name
    schedule_arn            = aws_scheduler_schedule.producer.arn
    schedule_expression     = aws_scheduler_schedule.producer.schedule_expression
    schedule_start_date     = aws_scheduler_schedule.producer.start_date
  }
}

