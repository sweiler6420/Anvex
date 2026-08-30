/**
 * The four security groups, and the chain they form.
 *
 * ```
 *   internet --80/443--> alb --8000--> app --5432--> db
 *                                       \--6379--> cache
 * ```
 *
 * Every ingress rule but the ALB's names a **source security group, not a CIDR**. That is
 * the difference between "the API may reach Postgres" and "anything that happens to be in
 * 10.0.0.0/16 may reach Postgres", and it survives a subnet being re-cut.
 *
 * The rules are `aws_vpc_security_group_*_rule` resources rather than inline `ingress`
 * blocks: inline blocks are authoritative, so any rule added out of band is silently
 * reverted on the next apply, and the pair of them cannot be mixed on one group.
 */

# ---------------------------------------------------------------------------------- ALB

resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "Public entrypoint. Terminates 80/443 and forwards to the API tasks."
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.name}-alb"
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from the internet."
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from the internet. Open whether or not a certificate is attached yet."
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_app" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Forward to the API tasks, and nowhere else."
  referenced_security_group_id = aws_security_group.app.id
  from_port                    = var.api_port
  to_port                      = var.api_port
  ip_protocol                  = "tcp"
}

# ---------------------------------------------------------------------------------- app

resource "aws_security_group" "app" {
  name        = "${var.name}-app"
  description = "The three ECS services: api, worker and beat."
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.name}-app"
  }
}

resource "aws_vpc_security_group_ingress_rule" "app_from_alb" {
  security_group_id            = aws_security_group.app.id
  description                  = "The ALB's health checks and forwarded requests."
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = var.api_port
  to_port                      = var.api_port
  ip_protocol                  = "tcp"
}

# Unrestricted egress, and this one really is unavoidable: the tasks call AlphaVantage and
# NewsAPI over HTTPS to addresses nobody controls, pull images from ECR, read Secrets
# Manager and ship logs to CloudWatch. Narrowing it means interface endpoints for four
# services at roughly $7/month each per AZ — a decision for a production ticket, not this one.
resource "aws_vpc_security_group_egress_rule" "app_all" {
  security_group_id = aws_security_group.app.id
  description       = "All outbound: third-party APIs, ECR, Secrets Manager, CloudWatch."
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# ----------------------------------------------------------------------------- postgres

resource "aws_security_group" "db" {
  name        = "${var.name}-db"
  description = "RDS Postgres. Reachable only from the app security group."
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.name}-db"
  }
}

resource "aws_vpc_security_group_ingress_rule" "db_from_app" {
  security_group_id            = aws_security_group.db.id
  description                  = "Postgres from the ECS tasks."
  referenced_security_group_id = aws_security_group.app.id
  from_port                    = var.postgres_port
  to_port                      = var.postgres_port
  ip_protocol                  = "tcp"
}

# -------------------------------------------------------------------------------- redis

resource "aws_security_group" "cache" {
  name        = "${var.name}-cache"
  description = "ElastiCache Redis: Celery's broker and result backend."
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.name}-cache"
  }
}

resource "aws_vpc_security_group_ingress_rule" "cache_from_app" {
  security_group_id            = aws_security_group.cache.id
  description                  = "Redis from the ECS tasks."
  referenced_security_group_id = aws_security_group.app.id
  from_port                    = var.redis_port
  to_port                      = var.redis_port
  ip_protocol                  = "tcp"
}

# Neither data-tier group has an egress rule, so both default to none. A database that
# cannot open an outbound connection is one fewer way for a compromise to leave the VPC —
# and unlike the app tier, nothing here needs to.
