variable "aws_region" {
  description = "AWS region to deploy resources into"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
  default     = "prod"
}

variable "project_name" {
  description = "Project identifier"
  type        = string
  default     = "cloud-cost-guardian"
}

variable "spike_threshold" {
  description = "Fractional increase threshold over baseline (e.g. 0.20 = 20%)"
  type        = number
  default     = 0.20
}

variable "min_spike_amount_usd" {
  description = "Minimum dollar spike required to trigger alerts"
  type        = number
  default     = 10.0
}

variable "lookback_days" {
  description = "Number of historical days for baseline calculation"
  type        = number
  default     = 14
}

variable "schedule_expression" {
  description = "EventBridge schedule expression"
  type        = string
  default     = "cron(0 8 * * ? *)" # Every day at 08:00 UTC
}

variable "slack_webhook_url" {
  description = "Slack incoming webhook URL"
  type        = string
  default     = ""
  sensitive   = true
}

variable "alert_email" {
  description = "Optional email address to receive SNS cost alerts"
  type        = string
  default     = ""
}

variable "demo_mode" {
  description = "Run with synthetic spend data instead of calling live Cost Explorer"
  type        = bool
  default     = false
}
