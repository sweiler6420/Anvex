/**
 * The load balancer, and the one health check that matters.
 *
 * `app/api/health.py` splits liveness from readiness and says why: `/health` touches
 * nothing, so a database outage never makes an orchestrator restart healthy containers;
 * `/health/ready` runs a real `SELECT 1` and answers 503 when it cannot. **A target group
 * polls the readiness endpoint** — its job is "should traffic go here", which is precisely
 * the question `/health/ready` answers. The container-level health check in `services.tf`
 * polls `/health` instead, for the mirror-image reason. Swapping the two would turn a
 * database blip into a restart loop, which is the failure the split exists to prevent.
 */

resource "aws_lb" "this" {
  name               = var.name
  load_balancer_type = "application"
  internal           = false
  subnets            = var.public_subnet_ids
  security_groups    = [var.alb_security_group_id]

  # An ALB that a `terraform destroy` in the wrong directory can delete takes the whole
  # public surface with it. Cheap insurance, and per-environment for the same reason
  # `postgres_deletion_protection` is: `false` in `local`, `true` in `dev`.
  enable_deletion_protection = var.alb_deletion_protection

  # Longer than any request the API makes, because an ALB that gives up mid-response
  # produces a 504 the application never sees and cannot log. The slowest path here is an
  # AlphaVantage-backed ingest, and those run in Celery, not in a request.
  idle_timeout = 60

  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "api" {
  name        = "${var.name}-api"
  vpc_id      = var.vpc_id
  port        = var.api_port
  protocol    = "HTTP"
  target_type = "ip" # `awsvpc` gives each task its own ENI and address.

  health_check {
    enabled             = true
    path                = "/health/ready"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  # Long enough for an in-flight request to finish, short enough that a deployment is not
  # dominated by waiting. The API holds no long-lived connections.
  deregistration_delay = 30

  # The target group is named after the service, so replacing it means replacing something
  # a listener rule points at. Create the new one first.
  lifecycle {
    create_before_destroy = true
  }
}

# ---------------------------------------------------------------------------- listeners

# Port 80 forwards when there is no certificate and redirects when there is. Without a
# domain there is nothing to get a certificate for, so both `envs/*.tfvars` leave
# `acm_certificate_arn` null and this is a plain forward — over HTTP, which is stated in
# `docs/aws-deployment.md` as a thing to fix before anything real is behind it.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  dynamic "default_action" {
    for_each = var.acm_certificate_arn == null ? [1] : []

    content {
      type             = "forward"
      target_group_arn = aws_lb_target_group.api.arn
    }
  }

  dynamic "default_action" {
    for_each = var.acm_certificate_arn == null ? [] : [1]

    content {
      type = "redirect"

      redirect {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }
}

resource "aws_lb_listener" "https" {
  count = var.acm_certificate_arn == null ? 0 : 1

  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.acm_certificate_arn

  # TLS 1.2 minimum, forward secrecy only. The `-Res-` policies are the ones AWS keeps
  # current; naming a fixed cipher list here would age badly.
  ssl_policy = "ELBSecurityPolicy-TLS13-1-2-Res-2021-06"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}
