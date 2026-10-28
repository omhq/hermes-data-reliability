variable "aws_profile" {
  description = "Locally authenticated AWS profile; SSO credentials are not Terraform inputs."
  type        = string
}

variable "aws_region" {
  description = "AWS region for this deployment."
  type        = string
  default     = "us-east-1"
}

variable "expected_account_id" {
  description = "Required deployment account allowlist; refuses credentials for another account."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "expected_account_id must be the confirmed 12-digit AWS account ID."
  }
}

variable "project_name" {
  description = "Prefix for lab resources."
  type        = string
  default     = "hermes-lab"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,39}$", var.project_name))
    error_message = "Use 3-40 lowercase letters, digits, or hyphens, starting with a letter."
  }
}

variable "instance_type" {
  description = "x86 T3 size; start at 4 GiB RAM, downsize after measuring."
  type        = string
  default     = "t3.medium"

  validation {
    condition     = contains(["t3.small", "t3.medium"], var.instance_type)
    error_message = "Choose t3.small (2 GiB RAM) or t3.medium (4 GiB RAM)."
  }
}

variable "ssh_cidr" {
  description = "Trusted SSH source IPv4 CIDR, usually your public IP followed by /32."
  type        = string

  validation {
    condition     = can(cidrnetmask(var.ssh_cidr)) && try(tonumber(split("/", var.ssh_cidr)[1]) >= 24, false)
    error_message = "Supply an IPv4 CIDR with prefix /24 or narrower; normally use your IP/32."
  }
}

variable "ssh_key_name" {
  description = "Existing EC2 key-pair name in the target account and region; use this OR ssh_public_key_path."
  type        = string
  default     = null

  validation {
    condition     = var.ssh_key_name == null ? true : length(trimspace(var.ssh_key_name)) > 0
    error_message = "ssh_key_name must be nonempty when supplied."
  }
}

variable "ssh_public_key_path" {
  description = "Local .pub file for Terraform to import; never a private key. Use this OR ssh_key_name."
  type        = string
  default     = null

  validation {
    condition     = var.ssh_public_key_path == null ? true : endswith(var.ssh_public_key_path, ".pub")
    error_message = "Only a public key file ending in .pub may be imported."
  }
}

variable "ami_id" {
  description = "Optional pinned Amazon Linux 2023 x86_64 AMI; otherwise resolve the current Amazon image at plan time."
  type        = string
  default     = null
}

variable "evidence_bucket_name" {
  description = "Existing bucket authorized for evidence reads under input/, output/, and runs/."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.evidence_bucket_name))
    error_message = "Supply an existing bucket name without an ARN, path, or wildcard."
  }
}

variable "evidence_log_group" {
  description = "Existing CloudWatch log group authorized for collection and investigation."
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z0-9/._#-]+$", var.evidence_log_group))
    error_message = "Supply a literal existing log group name without an ARN or wildcard."
  }
}
