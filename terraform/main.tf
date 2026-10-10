terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "CloudCostGuardian"
      Environment = var.environment
      ManagedBy   = "Terraform"
      Owner       = "FinOps"
    }
  }
}

# -----------------------------------------------------------------------------
# LAMBDA ARTIFACT PACKAGING
# -----------------------------------------------------------------------------
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../src"
  output_path = "${path.module}/dist/cost_guardian.zip"
}

# -----------------------------------------------------------------------------
# IAM ROLE & POLICIES (LEAST PRIVILEGE)
# -----------------------------------------------------------------------------
resource "aws_iam_role" "lambda_exec" {
  name = "${var.project_name}-lambda-exec-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_policy" "cost_explorer_access" {
  name        = "${var.project_name}-ce-access-${var.environment}"
  description = "Allows Lambda to query AWS Cost Explorer and publish alerts"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CostExplorerRead"
        Effect = "Allow"
        Action = [
          "ce:GetCostAndUsage",
          "ce:GetCostForecast",
          "ce:GetAnomalies",
          "ce:GetAnomalySubscriptions"
        ]
        Resource = "*"
      },
      {
        Sid    = "SNSPublish"
        Effect = "Allow"
        Action = [
          "sns:Publish"
        ]
        Resource = aws_sns_topic.cost_alerts.arn
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_policy_attach" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = aws_iam_policy.cost_explorer_access.arn
}

# -----------------------------------------------------------------------------
# SNS TOPIC FOR COST ALERTS
# -----------------------------------------------------------------------------
resource "aws_sns_topic" "cost_alerts" {
  name         = "${var.project_name}-alerts-${var.environment}"
  display_name = "Cloud Cost Guardian Anomaly Alerts"
}

resource "aws_sns_topic_subscription" "email_sub" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.cost_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# -----------------------------------------------------------------------------
# LAMBDA FUNCTION
# -----------------------------------------------------------------------------
resource "aws_lambda_function" "cost_guardian" {
  function_name    = "${var.project_name}-${var.environment}"
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256

  environment {
    variables = {
      SPIKE_THRESHOLD      = tostring(var.spike_threshold)
      MIN_SPIKE_AMOUNT_USD = tostring(var.min_spike_amount_usd)
      LOOKBACK_DAYS        = tostring(var.lookback_days)
      SLACK_WEBHOOK_URL    = var.slack_webhook_url
      SNS_TOPIC_ARN        = aws_sns_topic.cost_alerts.arn
      DEMO_MODE            = tostring(var.demo_mode)
    }
  }
}

resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/${aws_lambda_function.cost_guardian.function_name}"
  retention_in_days = 14
}

# -----------------------------------------------------------------------------
# EVENTBRIDGE (CRON SCHEDULE) - DAILY AT 08:00 UTC
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_event_rule" "daily_schedule" {
  name                = "${var.project_name}-daily-trigger"
  description         = "Triggers Cloud Cost Guardian every morning at 08:00 UTC"
  schedule_expression = var.schedule_expression
}

resource "aws_cloudwatch_event_target" "trigger_lambda" {
  rule      = aws_cloudwatch_event_rule.daily_schedule.name
  target_id = "TriggerCostGuardianLambda"
  arn       = aws_lambda_function.cost_guardian.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cost_guardian.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_schedule.arn
}
