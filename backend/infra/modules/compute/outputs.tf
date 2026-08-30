output "alb_dns_name" {
  description = "Public DNS name of the load balancer. Point a CNAME at it, or curl it directly."
  value       = aws_lb.this.dns_name
}

output "alb_zone_id" {
  description = "Hosted zone id, for a Route 53 alias record."
  value       = aws_lb.this.zone_id
}

output "cluster_name" {
  description = "ECS cluster name, for `aws ecs update-service` and `execute-command`."
  value       = aws_ecs_cluster.this.name
}

output "service_names" {
  description = "The three services, in the order a deploy touches them."
  value = [
    aws_ecs_service.api.name,
    aws_ecs_service.worker.name,
    aws_ecs_service.beat.name,
  ]
}

output "task_definition_families" {
  description = "Task definition families, for `aws ecs describe-task-definition`."
  value = {
    api    = aws_ecs_task_definition.api.family
    worker = aws_ecs_task_definition.worker.family
    beat   = aws_ecs_task_definition.beat.family
  }
}

output "log_group_names" {
  description = "CloudWatch log group per service."
  value       = { for service, group in aws_cloudwatch_log_group.service : service => group.name }
}

output "environment_variable_names" {
  description = <<-EOT
    Every environment variable the task definitions set, plain and secret together.

    Sorted, and exported because it is the deployment's half of the contract with
    `app/settings.py` — `terraform output` beside `Settings.model_fields` is the same
    comparison `test_infra_terraform.py` makes statically.
  EOT
  value       = sort(concat(keys(local.container_environment), keys(local.container_secrets)))
}
