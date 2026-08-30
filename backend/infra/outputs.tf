/**
 * What an operator needs after an apply, and nothing that would be a secret.
 *
 * No output resolves a Secrets Manager value, and the RDS master secret is exported as an
 * ARN only. `terraform output` is a thing people paste into chat.
 */

output "alb_dns_name" {
  description = "Public entrypoint. `curl http://<this>/health` is the first check after a deploy."
  value       = module.compute.alb_dns_name
}

output "alb_zone_id" {
  description = "Hosted zone id, for a Route 53 alias record."
  value       = module.compute.alb_zone_id
}

output "ecr_repository_url" {
  description = "Push target. One repository; all three services run this image."
  value       = module.registry.repository_url
}

output "ecs_cluster_name" {
  description = "For `aws ecs update-service` and `aws ecs execute-command`."
  value       = module.compute.cluster_name
}

output "ecs_service_names" {
  description = "The three services a deploy updates."
  value       = module.compute.service_names
}

output "ecs_task_definition_families" {
  description = "Task definition families per service."
  value       = module.compute.task_definition_families
}

output "log_group_names" {
  description = "CloudWatch log group per service."
  value       = module.compute.log_group_names
}

output "postgres_host" {
  description = <<-EOT
    The RDS endpoint.

    This is the whole of pointing host-side tooling at the database: `POSTGRES_HOST=<this>`
    in the environment and `scripts/migrate` reaches RDS instead of the compose stack
    (ANV-37). There is no second DSN.
  EOT
  value       = module.data.postgres_host
}

output "postgres_master_secret_arn" {
  description = "ARN of the RDS-managed master password secret. The value is never read by Terraform."
  value       = module.data.postgres_master_secret_arn
}

output "redis_host" {
  description = "ElastiCache primary endpoint."
  value       = module.data.redis_host
}

output "s3_bucket_name" {
  description = "The exports bucket. S3_BUCKET in the task definitions."
  value       = module.storage.bucket_name
}

output "s3_bucket_arn" {
  description = "ARN of the exports bucket."
  value       = module.storage.bucket_arn
}

output "app_iam_user_name" {
  description = "IAM user whose access key the app uses. No key is created by Terraform; see the deploy doc."
  value       = module.storage.app_iam_user_name
}

output "secrets_to_populate" {
  description = <<-EOT
    **Every ECS task fails to start until each of these has a value.**

    Terraform creates the named secret and the grant that opens it, never the contents —
    see `modules/secrets/main.tf`. `docs/aws-deployment.md` step 3 is the four commands.
  EOT
  value       = module.secrets.names
}

output "environment_variable_names" {
  description = "Every variable the task definitions set, plain and secret. Compare against `Settings`."
  value       = module.compute.environment_variable_names
}
