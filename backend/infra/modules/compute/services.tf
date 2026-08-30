/**
 * The three ECS services: `api`, `worker`, `beat`.
 *
 * Same image, same environment, three commands — the AWS reading of the same three compose
 * services. The task definitions are written out separately rather than generated from a
 * `for_each`, because the three differ in ways that are not parameters: only `api` has a
 * port mapping, a load balancer and a container health check, and only `beat` is forbidden
 * from ever running twice.
 *
 * **`beat` is the one with a rule attached.** `docker-compose.yml` says it plainly: "Running
 * two `beat` processes would double every scheduled job, so there is exactly one and it is
 * never scaled." A rolling deployment would ordinarily start the replacement before
 * stopping the original, which is exactly two beats. So its service sets
 * `deployment_minimum_healthy_percent = 0` and `deployment_maximum_percent = 100`, which
 * tells ECS to stop the old task *first*. The cost is a gap of a few seconds in which no
 * ticks are published, and that is free: every scheduled job is idempotent (`CLAUDE.md` §3)
 * and the next tick re-drives whatever the gap missed.
 */

locals {
  # The log configuration all three share, differing only in the group.
  log_configuration = {
    for service, group in aws_cloudwatch_log_group.service :
    service => {
      logDriver = "awslogs"
      options = {
        awslogs-group         = group.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "ecs"
      }
    }
  }
}

# ---------------------------------------------------------------------------------- api

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = local.image
      essential = true

      # No `command`: the image's own CMD is `uvicorn app.main:app --host 0.0.0.0 --port
      # 8000`, which is what should run. Compose overrides it only to add `--reload`.
      portMappings = [
        {
          containerPort = var.api_port
          protocol      = "tcp"
        },
      ]

      environment = local.environment_entries
      secrets     = local.secret_entries

      # Liveness, not readiness — see `alb.tf`. ECS does not inherit an image's HEALTHCHECK,
      # so the Dockerfile's is restated here in ECS's own shape (stdlib urllib, so the image
      # needs no curl).
      healthCheck = {
        command = [
          "CMD-SHELL",
          "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${var.api_port}/health', timeout=4).status == 200 else 1)\"",
        ]
        interval    = 15
        timeout     = 5
        retries     = 3
        startPeriod = 20
      }

      logConfiguration = local.log_configuration["api"]

      # uvicorn handles SIGTERM by draining; 30s is longer than any request it serves.
      stopTimeout = 30
    },
  ])
}

resource "aws_ecs_service" "api" {
  name            = "api"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.app_security_group_id]
    assign_public_ip = false # Private subnets; egress is the NAT gateway's job.
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = var.api_port
  }

  # The app opens no socket at startup (the engine is lazy), so it is serving in seconds.
  # This is the window before a failing target group check counts against the task.
  health_check_grace_period_seconds = 60

  # A deployment that never becomes healthy rolls itself back rather than sitting in
  # `IN_PROGRESS` until somebody notices.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # `aws ecs execute-command` — the only way to reach RDS from inside the VPC without a
  # bastion, and therefore how `alembic upgrade head` runs. See `docs/aws-deployment.md`.
  enable_execute_command = true
  propagate_tags         = "SERVICE"

  depends_on = [aws_lb_listener.http]

  # A deploy pushes a new image and updates the service outside Terraform, so the running
  # revision is not Terraform's to own. Without this, the next `apply` would roll the
  # service back to whatever revision the state remembers.
  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }
}

# ------------------------------------------------------------------------------- worker

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([
    {
      name      = "worker"
      image     = local.image
      essential = true
      command   = local.service_commands["worker"]

      environment = local.environment_entries
      secrets     = local.secret_entries

      # No port mapping and no health check. A worker serves no HTTP; compose replaces the
      # image's check with `celery inspect ping`, which needs a round trip through the
      # broker and is slower and flakier than it is worth as a task-level probe. The
      # service's own "is the task running" is the check here, and the `jobs.health.ping`
      # beat task every five minutes is what actually says the pipeline works.
      logConfiguration = local.log_configuration["worker"]

      # `task_acks_late` means a graceful stop leaves unfinished messages on the queue for
      # redelivery. This is the window Celery gets to finish what it has in hand first —
      # longer than the API's, because a job is longer than a request. It must stay under
      # the broker's visibility timeout for the same reason the time limit does.
      stopTimeout = 120
    },
  ])
}

resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.app_security_group_id]
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  enable_execute_command = true
  propagate_tags         = "SERVICE"

  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }
}

# --------------------------------------------------------------------------------- beat

resource "aws_ecs_task_definition" "beat" {
  family                   = "${var.name}-beat"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.beat_cpu
  memory                   = var.beat_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([
    {
      name      = "beat"
      image     = local.image
      essential = true
      command   = local.service_commands["beat"]

      environment = local.environment_entries
      secrets     = local.secret_entries

      # No health check, and compose gives the same answer for the same reason: beat exposes
      # no inspect interface and consumes nothing, so there is nothing to ask it. Its
      # liveness shows up as the tasks it publishes.
      logConfiguration = local.log_configuration["beat"]

      stopTimeout = 30
    },
  ])
}

resource "aws_ecs_service" "beat" {
  name            = "beat"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.beat.arn
  launch_type     = "FARGATE"

  # One. Not a variable, because there is no value other than 1 that is ever correct — see
  # the header and `docker-compose.yml`.
  desired_count = 1

  # Stop the old task before starting the new one. This is what keeps a rolling deployment
  # from briefly running two schedulers and publishing every tick twice.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.app_security_group_id]
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  enable_execute_command = true
  propagate_tags         = "SERVICE"

  lifecycle {
    # `desired_count` is **not** ignored here: unlike the other two, drift in this number is
    # a bug, and Terraform putting it back to 1 is the correct response.
    ignore_changes = [task_definition]
  }
}
