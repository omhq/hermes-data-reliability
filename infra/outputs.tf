output "instance_id" {
  description = "EC2 instance running the experiment."
  value       = aws_instance.hermes.id
}

output "public_ip" {
  description = "Current SSH address; it can change after a stop/start."
  value       = aws_instance.hermes.public_ip
}

output "ssh_destination" {
  description = "Use ssh -i <local-private-key-path> with this destination."
  value       = "ec2-user@${aws_instance.hermes.public_ip}"
}

output "selected_ami_id" {
  description = "Set ami_id to this value in local tfvars to pin subsequent plans."
  value       = aws_instance.hermes.ami
}

output "observer_role_name" {
  description = "Role to extend with scoped observation permissions as the lab grows."
  value       = aws_iam_role.hermes.name
}

output "workspace_path" {
  value = "/srv/hermes-lab"
}
output "pipeline" {
  value = {
    bucket                  = aws_s3_bucket.evidence.id
    log_group               = aws_cloudwatch_log_group.processor.name
    function                = aws_lambda_function.processor.function_name
    producer_function       = aws_lambda_function.producer.function_name
    producer_log_group      = aws_cloudwatch_log_group.producer.name
    queue_url               = aws_sqs_queue.input.id
    queue_arn               = aws_sqs_queue.input.arn
    dead_letter_url         = aws_sqs_queue.dead_letter.id
    event_source_mapping_id = aws_lambda_event_source_mapping.processor.uuid
    region                  = var.aws_region
    schedule_enabled        = var.pipeline_schedule_enabled
  }
}

