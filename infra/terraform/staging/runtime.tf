variable "hostname" {
  type = string
}
variable "certificate_arn" {
  type = string
}
variable "image_digests" {
  type = object({ web = string, backend = string })
  validation {
    condition     = alltrue([for digest in values(var.image_digests) : can(regex("^sha256:[a-f0-9]{64}$", digest))])
    error_message = "Use immutable SHA-256 image digests."
  }
}
variable "secret_arns" {
  description = "Existing Secrets Manager entries containing complete TLS-enforcing DSNs."
  type        = object({ application = string, reporting = string, migration = string, broker = string })
}
variable "alarm_topic_arn" {
  description = "Existing operator SNS topic for alarm delivery."
  type        = string
}
resource "aws_ecr_repository" "images" {
  for_each             = toset(["web", "backend"])
  name                 = "${var.name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "KMS"
  }
}
locals {
  tasks = {
    web = { image = "web", cpu = 256, memory = 512, command = [], secrets = {} }
    api = {
      image   = "backend", cpu = 512, memory = 1024,
      command = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
      secrets = { DATABASE_URL = var.secret_arns.application, REPORTING_DATABASE_URL = var.secret_arns.reporting, REDIS_URL = var.secret_arns.broker }
    }
    worker = {
      image   = "backend", cpu = 1024, memory = 2048,
      command = ["celery", "-A", "app.jobs.celery_app:celery_app", "worker", "--concurrency=2", "--loglevel=INFO"]
      secrets = { DATABASE_URL = var.secret_arns.application, REPORTING_DATABASE_URL = var.secret_arns.reporting, REDIS_URL = var.secret_arns.broker }
    }
    scheduler = {
      image   = "backend", cpu = 256, memory = 512,
      command = ["celery", "-A", "app.jobs.celery_app:celery_app", "beat", "--schedule=/tmp/celerybeat", "--loglevel=INFO"]
      secrets = { REDIS_URL = var.secret_arns.broker }
    }
    migration = {
      image   = "backend", cpu = 256, memory = 512,
      command = ["alembic", "upgrade", "head"]
      secrets = { MIGRATION_DATABASE_URL = var.secret_arns.migration }
    }
  }
  report_access = { api = ["s3:GetObject"], worker = ["s3:PutObject", "s3:DeleteObject"] }
  assume_task = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}
resource "aws_iam_role" "execution" {
  for_each           = local.tasks
  name               = "${var.name}-exec-${each.key}"
  assume_role_policy = local.assume_task
}
resource "aws_iam_role_policy" "execution" {
  for_each = local.tasks
  name     = "image-logs-secrets"
  role     = aws_iam_role.execution[each.key].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = aws_ecr_repository.images[each.value.image].arn },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.application[each.key].arn}:log-stream:*" }
      ], length(each.value.secrets) == 0 ? [] : [
      { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = distinct(values(each.value.secrets)) }
    ])
  })
}
resource "aws_iam_role" "task" {
  for_each           = local.tasks
  name               = "${var.name}-task-${each.key}"
  assume_role_policy = local.assume_task
}
resource "aws_iam_role_policy" "reports" {
  for_each = local.report_access
  name     = "report-artifacts"
  role     = aws_iam_role.task[each.key].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = each.value, Resource = "${aws_s3_bucket.reports.arn}/*" },
      { Effect = "Allow", Action = each.key == "api" ? ["kms:Decrypt"] : ["kms:GenerateDataKey"], Resource = aws_kms_key.reports.arn,
      Condition = { StringEquals = { "kms:ViaService" = "s3.${var.region}.amazonaws.com" } } }
    ]
  })
}
resource "aws_ecs_cluster" "staging" {
  name = var.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}
resource "aws_ecs_task_definition" "service" {
  for_each                 = local.tasks
  family                   = "${var.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.execution[each.key].arn
  task_role_arn            = aws_iam_role.task[each.key].arn
  volume {
    name = "scratch"
  }
  container_definitions = jsonencode([merge({
    name                   = each.key
    image                  = "${aws_ecr_repository.images[each.value.image].repository_url}@${var.image_digests[each.value.image]}"
    essential              = true
    readonlyRootFilesystem = true
    stopTimeout            = each.key == "worker" ? 120 : 30
    linuxParameters        = { initProcessEnabled = true, capabilities = { drop = ["ALL"] } }
    mountPoints            = [{ sourceVolume = "scratch", containerPath = "/tmp", readOnly = false }]
    portMappings           = contains(keys(local.ports), each.key) ? [{ containerPort = local.ports[each.key], protocol = "tcp" }] : []
    secrets                = [for name, arn in each.value.secrets : { name = name, valueFrom = arn }]
    environment = contains(keys(local.report_access), each.key) ? [
      { name = "REPORT_STORAGE", value = "s3" },
      { name = "REPORT_S3_BUCKET", value = aws_s3_bucket.reports.bucket },
      { name = "CORS_ORIGINS", value = jsonencode(["https://${var.hostname}"]) },
      { name = "AI_PROVIDER", value = "disabled" },
      { name = "COOKIE_SECURE", value = "true" }
    ] : []
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.application[each.key].name
        awslogs-region        = var.region
        awslogs-stream-prefix = "ecs"
      }
    }
  }, length(each.value.command) == 0 ? {} : { command = each.value.command })])
}
resource "aws_ecs_service" "staging" {
  for_each                           = { for name, task in local.tasks : name => task if name != "migration" }
  name                               = each.key
  cluster                            = aws_ecs_cluster.staging.id
  task_definition                    = aws_ecs_task_definition.service[each.key].arn
  launch_type                        = "FARGATE"
  desired_count                      = var.start_services ? 1 : 0
  deployment_minimum_healthy_percent = each.key == "scheduler" ? 0 : 100
  deployment_maximum_percent         = each.key == "scheduler" ? 100 : 200
  health_check_grace_period_seconds  = contains(keys(local.ports), each.key) ? 60 : null
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.service[each.key].id]
    assign_public_ip = false
  }
  dynamic "load_balancer" {
    for_each = contains(keys(local.ports), each.key) ? [each.key] : []
    content {
      target_group_arn = aws_lb_target_group.http[load_balancer.value].arn
      container_name   = load_balancer.value
      container_port   = local.ports[load_balancer.value]
    }
  }
  depends_on = [aws_lb_listener_rule.api, aws_iam_role_policy.execution]
}
variable "start_services" {
  description = "Enable after the separate bootstrap and migration checks."
  type        = bool
  default     = false
}
resource "aws_lb" "edge" {
  name                       = var.name
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.service["edge"].id]
  subnets                    = aws_subnet.private[*].id
  internal                   = true
  drop_invalid_header_fields = true
  enable_deletion_protection = true
}
resource "aws_lb_target_group" "http" {
  for_each    = local.ports
  name        = "${var.name}-${each.key}"
  port        = each.value
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.staging.id
  health_check {
    path    = each.key == "api" ? "/api/v1/ready" : "/"
    matcher = "200"
  }
}
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.edge.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.certificate_arn
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.http["web"].arn
  }
}
resource "aws_lb_listener_rule" "api" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 10
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.http["api"].arn
  }
  condition {
    path_pattern {
      values = ["/api/v1/*"]
    }
  }
}
output "operation_endpoints" {
  value = {
    alb                      = aws_lb.edge.dns_name
    postgres                 = aws_db_instance.postgres.address
    broker                   = aws_elasticache_replication_group.broker.primary_endpoint_address
    repositories             = { for name, repo in aws_ecr_repository.images : name => repo.repository_url }
    cluster                  = aws_ecs_cluster.staging.name
    migration_task           = aws_ecs_task_definition.service["migration"].arn
    migration_subnets        = aws_subnet.private[*].id
    migration_security_group = aws_security_group.service["migration"].id
  }
}
