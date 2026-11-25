locals {
  log_metrics = {
    api_latency  = { group = "api", pattern = "{ $.event = \"api.request\" }", value = "$.duration_ms", unit = "Milliseconds" }
    api_errors   = { group = "api", pattern = "{ ($.event = \"api.request\") && ($.status >= 500) }", value = "1", unit = "Count" }
    db_latency   = { group = "api", pattern = "{ $.event = \"db.query\" }", value = "$.duration_ms", unit = "Milliseconds" }
    job_retry    = { group = "worker", pattern = "{ ($.event = \"job.execution\") && ($.state = \"RETRY\") }", value = "1", unit = "Count" }
    job_failure  = { group = "worker", pattern = "{ ($.event = \"job.outcome\") && ($.status = \"failed\") }", value = "1", unit = "Count" }
    llm_tokens   = { group = "api", pattern = "{ ($.event = \"ai.provider\") && ($.tokens = *) }", value = "$.tokens", unit = "Count" }
    queue_depth  = { group = "api", pattern = "{ $.event = \"broker.queue\" }", value = "$.default_queue_depth", unit = "Count" }
    job_wait     = { group = "worker", pattern = "{ $.event = \"job.delivery\" }", value = "$.wait_ms", unit = "Milliseconds" }
    job_runtime  = { group = "worker", pattern = "{ $.event = \"job.execution\" }", value = "$.duration_ms", unit = "Milliseconds" }
    recon_issues = { group = "worker", pattern = "{ $.event = \"reconciliation.summary\" }", value = "$.issue_count", unit = "Count" }
    task_failure = { group = "worker", pattern = "{ ($.event = \"job.execution\") && ($.state = \"FAILURE\") }", value = "1", unit = "Count" }
  }
  metrics_namespace = "InsightForge/${var.name}"
}
resource "aws_cloudwatch_log_metric_filter" "operations" {
  for_each       = local.log_metrics
  name           = each.key
  log_group_name = aws_cloudwatch_log_group.application[each.value.group].name
  pattern        = each.value.pattern
  metric_transformation {
    name      = each.key
    namespace = local.metrics_namespace
    value     = each.value.value
    unit      = each.value.unit
  }
}
resource "aws_cloudwatch_metric_alarm" "error" {
  for_each            = toset(["api_errors", "job_failure", "job_retry", "task_failure"])
  alarm_name          = "${var.name}-${each.key}"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  period              = 300
  threshold           = 3
  namespace           = local.metrics_namespace
  metric_name         = aws_cloudwatch_log_metric_filter.operations[each.key].metric_transformation[0].name
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
}
resource "aws_cloudwatch_metric_alarm" "unhealthy" {
  for_each            = local.ports
  alarm_name          = "${var.name}-${each.key}-unavailable"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  period              = 60
  threshold           = 1
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HealthyHostCount"
  statistic           = "Maximum"
  dimensions          = { LoadBalancer = aws_lb.edge.arn_suffix, TargetGroup = aws_lb_target_group.http[each.key].arn_suffix }
  treat_missing_data  = var.start_services ? "breaching" : "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "database_cpu" {
  alarm_name          = "${var.name}-db-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  period              = 300
  threshold           = 80
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.postgres.identifier }
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "queue_depth" {
  alarm_name          = "${var.name}-queue-depth"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  period              = 300
  threshold           = 50
  namespace           = local.metrics_namespace
  metric_name         = aws_cloudwatch_log_metric_filter.operations["queue_depth"].metric_transformation[0].name
  statistic           = "Average"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "delivery_wait" {
  alarm_name          = "${var.name}-job-delivery-wait"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  period              = 300
  threshold           = 30000
  namespace           = local.metrics_namespace
  metric_name         = aws_cloudwatch_log_metric_filter.operations["job_wait"].metric_transformation[0].name
  extended_statistic  = "p95"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
}
