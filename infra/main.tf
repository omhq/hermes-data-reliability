data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ami" "amazon_linux" {
  count       = var.ami_id == null ? 1 : 0
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "root-device-type"
    values = ["ebs"]
  }
}

data "aws_key_pair" "existing" {
  count    = var.ssh_key_name == null ? 0 : 1
  key_name = var.ssh_key_name
}

resource "aws_key_pair" "imported" {
  count      = var.ssh_public_key_path == null ? 0 : 1
  key_name   = "${var.project_name}-ssh"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

resource "aws_vpc" "lab" {
  cidr_block           = "10.73.0.0/24"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = var.project_name }
}

resource "aws_subnet" "lab" {
  vpc_id            = aws_vpc.lab.id
  cidr_block        = "10.73.0.0/26"
  availability_zone = sort(data.aws_availability_zones.available.names)[0]
  tags              = { Name = "${var.project_name}-public" }
}

resource "aws_internet_gateway" "lab" {
  vpc_id = aws_vpc.lab.id
}

resource "aws_route_table" "lab" {
  vpc_id = aws_vpc.lab.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.lab.id
  }
}

resource "aws_route_table_association" "lab" {
  subnet_id      = aws_subnet.lab.id
  route_table_id = aws_route_table.lab.id
}

resource "aws_security_group" "host" {
  name_prefix = "${var.project_name}-"
  description = "SSH from the operator and outbound HTTPS for packages and APIs"
  vpc_id      = aws_vpc.lab.id
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  security_group_id = aws_security_group.host.id
  description       = "Operator SSH access"
  cidr_ipv4         = var.ssh_cidr
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.host.id
  description       = "Outbound HTTPS for AWS, model APIs, and image/package downloads"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_instance" "hermes" {
  ami                         = var.ami_id != null ? var.ami_id : data.aws_ami.amazon_linux[0].id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.lab.id
  vpc_security_group_ids      = [aws_security_group.host.id]
  associate_public_ip_address = true
  key_name                    = var.ssh_key_name != null ? data.aws_key_pair.existing[0].key_name : try(aws_key_pair.imported[0].key_name, null)
  iam_instance_profile        = aws_iam_instance_profile.hermes.name
  user_data                   = file("${path.module}/bootstrap.sh")
  user_data_replace_on_change = true

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "disabled"
  }

  # Avoid surplus CPU-credit charges in this inexpensive lab.
  credit_specification {
    cpu_credits = "standard"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 30
    encrypted             = true
    delete_on_termination = true
  }

  lifecycle {
    precondition {
      condition     = (var.ssh_key_name != null) != (var.ssh_public_key_path != null)
      error_message = "Supply exactly one of ssh_key_name or ssh_public_key_path."
    }
  }

  tags = { Name = "${var.project_name}-hermes" }

  # Cloud-init needs the route and outbound rule to download packages.
  depends_on = [aws_route_table_association.lab, aws_vpc_security_group_egress_rule.https]
}
