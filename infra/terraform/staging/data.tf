resource "aws_db_subnet_group" "postgres" {
  name       = var.name
  subnet_ids = aws_subnet.private[*].id
}
resource "aws_db_parameter_group" "postgres" {
  name   = var.name
  family = "postgres16"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}
resource "aws_db_instance" "postgres" {
  identifier                      = var.name
  engine                          = "postgres"
  engine_version                  = "16"
  instance_class                  = "db.t4g.micro"
  allocated_storage               = 20
  max_allocated_storage           = 100
  storage_type                    = "gp3"
  storage_encrypted               = true
  db_name                         = "insightforge"
  username                        = "bootstrap_admin"
  manage_master_user_password     = true
  db_subnet_group_name            = aws_db_subnet_group.postgres.name
  parameter_group_name            = aws_db_parameter_group.postgres.name
  vpc_security_group_ids          = [aws_security_group.service["database"].id]
  publicly_accessible             = false
  backup_retention_period         = 7
  deletion_protection             = true
  skip_final_snapshot             = false
  final_snapshot_identifier       = "${var.name}-final"
  copy_tags_to_snapshot           = true
  enabled_cloudwatch_logs_exports = ["postgresql"]
}
resource "aws_elasticache_subnet_group" "broker" {
  name       = var.name
  subnet_ids = aws_subnet.private[*].id
}
variable "broker_password" {
  description = "Externally supplied Redis AUTH token. Protected state is required."
  type        = string
  sensitive   = true
  nullable    = false
  validation {
    condition     = can(regex("^[A-Za-z0-9]{32,128}$", var.broker_password))
    error_message = "Use a generated 32–128 character alphanumeric Redis token."
  }
}
resource "aws_elasticache_replication_group" "broker" {
  replication_group_id       = var.name
  description                = "Dedicated staging Celery broker"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = "cache.t4g.micro"
  num_cache_clusters         = 1
  subnet_group_name          = aws_elasticache_subnet_group.broker.name
  security_group_ids         = [aws_security_group.service["broker"].id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  transit_encryption_mode    = "required"
  auth_token                 = var.broker_password
  snapshot_retention_limit   = 1
}
resource "aws_s3_bucket_versioning" "reports" {
  bucket = aws_s3_bucket.reports.id
  versioning_configuration {
    status = "Enabled"
  }
}
resource "aws_s3_bucket_lifecycle_configuration" "reports" {
  bucket     = aws_s3_bucket.reports.id
  depends_on = [aws_s3_bucket_versioning.reports]
  rule {
    id     = "expire-superseded-artifacts"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}
