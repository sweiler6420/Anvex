output "postgres_host" {
  description = "RDS endpoint address. This is what POSTGRES_HOST is set to."
  value       = aws_db_instance.this.address
}

output "postgres_port" {
  description = "RDS port. This is what POSTGRES_PORT is set to."
  value       = aws_db_instance.this.port
}

output "postgres_master_secret_arn" {
  description = <<-EOT
    ARN of the Secrets Manager secret RDS created and owns.

    The value is a JSON document with `username` and `password` keys, so ECS pulls the
    password out with the `:password::` suffix. Nothing here ever reads it.
  EOT
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}

output "redis_host" {
  description = "Primary endpoint address. This is what REDIS_HOST is set to."
  value       = aws_elasticache_replication_group.this.primary_endpoint_address
}

output "redis_port" {
  description = "Redis port. This is what REDIS_PORT is set to."
  value       = aws_elasticache_replication_group.this.port
}
