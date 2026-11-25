resource "aws_vpc_endpoint" "interface" {
  for_each            = toset(["ecr.api", "ecr.dkr", "logs", "secretsmanager"])
  vpc_id              = aws_vpc.staging.id
  vpc_endpoint_type   = "Interface"
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.service["endpoints"].id]
  private_dns_enabled = true
}
resource "aws_vpc_security_group_ingress_rule" "endpoint" {
  for_each                     = local.tasks
  security_group_id            = aws_security_group.service["endpoints"].id
  referenced_security_group_id = aws_security_group.service[each.key].id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}
resource "aws_vpc_security_group_egress_rule" "endpoint" {
  for_each                     = local.tasks
  security_group_id            = aws_security_group.service[each.key].id
  referenced_security_group_id = aws_security_group.service["endpoints"].id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}
resource "aws_vpc_security_group_egress_rule" "s3" {
  for_each          = local.tasks
  security_group_id = aws_security_group.service[each.key].id
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
