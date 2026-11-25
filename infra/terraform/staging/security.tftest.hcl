mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = {
      names = ["us-east-1a", "us-east-1b"]
    }
  }
}

variables {
  name            = "insightforge-local-test"
  hostname        = "staging.example.test"
  certificate_arn = "arn:aws:acm:us-east-1:000000000000:certificate/00000000-0000-4000-8000-000000000000"
  alarm_topic_arn = "arn:aws:sns:us-east-1:000000000000:synthetic"
  ingress_cidrs   = ["192.0.2.0/24"]
  broker_password = "SyntheticTerraformTestValueOnly0000"
  image_digests = {
    web     = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    backend = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  }
  secret_arns = {
    application = "arn:aws:secretsmanager:us-east-1:000000000000:secret:synthetic-app"
    reporting   = "arn:aws:secretsmanager:us-east-1:000000000000:secret:synthetic-read"
    migration   = "arn:aws:secretsmanager:us-east-1:000000000000:secret:synthetic-migration"
    broker      = "arn:aws:secretsmanager:us-east-1:000000000000:secret:synthetic-broker"
  }
}

run "closed_staging_defaults" {
  command = plan

  assert {
    condition     = aws_lb.edge.internal && !aws_db_instance.postgres.publicly_accessible
    error_message = "The edge and database must remain private."
  }

  assert {
    condition = alltrue([
      for service in aws_ecs_service.staging :
      service.desired_count == 0 && !service.network_configuration[0].assign_public_ip
    ])
    error_message = "Staging must bootstrap with services stopped, in private networking."
  }

  assert {
    condition = (
      aws_elasticache_replication_group.broker.transit_encryption_mode == "required" &&
      aws_elasticache_replication_group.broker.at_rest_encryption_enabled
    )
    error_message = "The broker must require TLS and at-rest encryption."
  }
}
