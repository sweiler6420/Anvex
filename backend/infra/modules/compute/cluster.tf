/**
 * The ECS cluster and the three log groups.
 */

resource "aws_ecs_cluster" "this" {
  name = var.name

  setting {
    name = "containerInsights"
    # Billed per metric per hour and there are a lot of metrics. The task-level CPU and
    # memory metrics ECS publishes for free are enough to size the services; Insights is
    # what you turn on when you have a question those cannot answer.
    value = var.enable_container_insights ? "enabled" : "disabled"
  }
}

# FARGATE and FARGATE_SPOT are both attached so a service can move to Spot without a cluster
# change. Nothing here uses Spot yet: an interrupted `beat` is a missed tick, and an
# interrupted `worker` is `acks_late` doing its job, but choosing that is a ticket with a
# number attached rather than a default.
resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 0
  }
}

# One group per service rather than one shared group with three prefixes: retention,
# subscription filters and metric filters are all per-group, and "everything the worker
# said" is the query you actually want to run.
resource "aws_cloudwatch_log_group" "service" {
  for_each = local.service_sizes

  name              = "/ecs/${var.name}/${each.key}"
  retention_in_days = var.log_retention_days
}
