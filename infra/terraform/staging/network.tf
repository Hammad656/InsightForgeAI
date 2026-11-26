data "aws_availability_zones" "available" {
  state = "available"
}
locals {
  zones = slice(data.aws_availability_zones.available.names, 0, 2)
  ports = { web = 3000, api = 8000 }
}
resource "aws_vpc" "staging" {
  cidr_block           = "10.72.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
}
resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.staging.id
  availability_zone = local.zones[count.index]
  cidr_block        = cidrsubnet(aws_vpc.staging.cidr_block, 8, count.index + 10)
}
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.staging.id
}
resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}
resource "aws_vpc_endpoint" "s3" {
  vpc_id          = aws_vpc.staging.id
  service_name    = "com.amazonaws.${var.region}.s3"
  route_table_ids = [aws_route_table.private.id]
}
variable "ingress_cidrs" {
  description = "Approved IPv4 operator CIDRs for staging HTTPS."
  type        = set(string)
  validation {
    condition     = length(var.ingress_cidrs) > 0 && alltrue([for cidr in var.ingress_cidrs : can(cidrnetmask(cidr)) && !endswith(cidr, "/0")])
    error_message = "Provide at least one restricted IPv4 CIDR."
  }
}
resource "aws_security_group" "service" {
  for_each = toset(["edge", "web", "api", "worker", "scheduler", "migration", "database", "broker", "endpoints"])
  name     = "${var.name}-${each.key}"
  vpc_id   = aws_vpc.staging.id
}
resource "aws_vpc_security_group_ingress_rule" "https" {
  for_each          = var.ingress_cidrs
  security_group_id = aws_security_group.service["edge"].id
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
locals {
  connections = {
    edge_web         = ["edge", "web", 3000]
    edge_api         = ["edge", "api", 8000]
    api_db           = ["api", "database", 5432]
    worker_db        = ["worker", "database", 5432]
    migration_db     = ["migration", "database", 5432]
    api_broker       = ["api", "broker", 6379]
    worker_broker    = ["worker", "broker", 6379]
    scheduler_broker = ["scheduler", "broker", 6379]
  }
}
resource "aws_vpc_security_group_ingress_rule" "internal" {
  for_each                     = local.connections
  security_group_id            = aws_security_group.service[each.value[1]].id
  referenced_security_group_id = aws_security_group.service[each.value[0]].id
  ip_protocol                  = "tcp"
  from_port                    = each.value[2]
  to_port                      = each.value[2]
}
resource "aws_vpc_security_group_egress_rule" "internal" {
  for_each                     = local.connections
  security_group_id            = aws_security_group.service[each.value[0]].id
  referenced_security_group_id = aws_security_group.service[each.value[1]].id
  ip_protocol                  = "tcp"
  from_port                    = each.value[2]
  to_port                      = each.value[2]
}
