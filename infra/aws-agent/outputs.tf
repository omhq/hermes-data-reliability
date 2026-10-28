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
