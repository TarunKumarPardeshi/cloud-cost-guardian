output "lambda_function_name" {
  description = "Name of deployed Lambda function"
  value       = aws_lambda_function.cost_guardian.function_name
}

output "lambda_function_arn" {
  description = "ARN of deployed Lambda function"
  value       = aws_lambda_function.cost_guardian.arn
}

output "sns_topic_arn" {
  description = "ARN of the SNS cost alerts topic"
  value       = aws_sns_topic.cost_alerts.arn
}

output "eventbridge_rule_name" {
  description = "Name of EventBridge cron trigger"
  value       = aws_cloudwatch_event_rule.daily_schedule.name
}
