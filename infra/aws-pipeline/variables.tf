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

variable "pipeline_schedule_enabled" {
  description = "Enable the producer schedule after the S3/SQS processor and evidence checks pass."
  type        = bool
  default     = false
}

variable "failure_probability" {
  description = "Probability of a deterministic malformed-input failure per scheduled run."
  type        = number
  default     = 0.2

  validation {
    condition     = var.failure_probability >= 0 && var.failure_probability <= 1
    error_message = "Use a probability between zero and one."
  }
}
