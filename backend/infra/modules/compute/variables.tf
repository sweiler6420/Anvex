variable "name" {
  description = "Name prefix (`anvex-dev`)."
  type        = string
}

variable "environment" {
  description = "Environment name. Becomes ANVEX_ENV in every container."
  type        = string
}

variable "partition" {
  description = "AWS partition, from `data.aws_partition`. Used to build the managed-policy ARN without writing one down."
  type        = string
}

variable "region" {
  description = "Region, used for the awslogs driver's `awslogs-region`."
  type        = string
}

# ---------------------------------------------------------------------------- placement

variable "vpc_id" {
  description = "VPC the target group is created in."
  type        = string
}

variable "public_subnet_ids" {
  description = "Subnets the ALB lives in."
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "Subnets the task ENIs are created in."
  type        = list(string)
}

variable "alb_security_group_id" {
  description = "Security group for the load balancer."
  type        = string
}

variable "app_security_group_id" {
  description = "Security group shared by all three services."
  type        = string
}

# ------------------------------------------------------------------------------- image

variable "image_repository_url" {
  description = "ECR repository URL. All three services run this image."
  type        = string
}

variable "image_repository_arn" {
  description = "ECR repository ARN, for the execution role's pull grant."
  type        = string
}

variable "image_tag" {
  description = "Tag all three services run."
  type        = string
}

variable "cpu_architecture" {
  description = "`X86_64` or `ARM64`. Must match what was pushed."
  type        = string
}

# ------------------------------------------------------------------------------- sizing

variable "api_cpu" {
  description = "Fargate CPU units for the API task."
  type        = number
}

variable "api_memory" {
  description = "Fargate memory for the API task, in MiB."
  type        = number
}

variable "api_desired_count" {
  description = "How many API tasks to run."
  type        = number
}

variable "worker_cpu" {
  description = "Fargate CPU units for the worker task."
  type        = number
}

variable "worker_memory" {
  description = "Fargate memory for the worker task, in MiB."
  type        = number
}

variable "worker_desired_count" {
  description = "How many worker tasks to run."
  type        = number
}

variable "worker_concurrency" {
  description = "`--concurrency` for the prefork pool."
  type        = number
}

variable "beat_cpu" {
  description = "Fargate CPU units for the beat task."
  type        = number
}

variable "beat_memory" {
  description = "Fargate memory for the beat task, in MiB."
  type        = number
}

# ----------------------------------------------------------------------------- runtime

variable "api_port" {
  description = "Port the API container listens on."
  type        = number
}

variable "acm_certificate_arn" {
  description = "ACM certificate for the HTTPS listener, or null for HTTP only."
  type        = string
}

variable "alb_deletion_protection" {
  description = "Refuse to delete the load balancer. The public surface's equivalent of the database's."
  type        = bool
}

variable "enable_container_insights" {
  description = "ECS Container Insights."
  type        = bool
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the three log groups."
  type        = number
}

variable "log_level" {
  description = "LOG_LEVEL."
  type        = string
}

# ------------------------------------------------------------- the environment contract

variable "postgres_host" {
  description = "POSTGRES_HOST — the RDS endpoint."
  type        = string
}

variable "postgres_port" {
  description = "POSTGRES_PORT."
  type        = number
}

variable "postgres_user" {
  description = "POSTGRES_USER."
  type        = string
}

variable "postgres_db" {
  description = "POSTGRES_DB."
  type        = string
}

variable "postgres_schema" {
  description = "POSTGRES_SCHEMA."
  type        = string
}

variable "postgres_master_secret_arn" {
  description = "RDS-managed secret. POSTGRES_PASSWORD is pulled from its `password` key."
  type        = string
}

variable "redis_host" {
  description = "REDIS_HOST — the ElastiCache primary endpoint."
  type        = string
}

variable "redis_port" {
  description = "REDIS_PORT."
  type        = number
}

variable "s3_bucket" {
  description = "S3_BUCKET."
  type        = string
}

variable "secret_arns" {
  description = <<-EOT
    Secret suffix => ARN, from `modules/secrets`.

    The keys are contract: `locals.tf` looks up `jwt-signing-key`, `alphavantage`,
    `newsapi` and `s3-credentials` by name, so renaming one there fails the plan here.
  EOT
  type        = map(string)
}

variable "api_cors_origins" {
  description = "Origins, joined into API_CORS_ORIGINS."
  type        = list(string)
}

variable "jwt_algorithm" {
  description = "JWT_ALGORITHM."
  type        = string
}

variable "jwt_access_token_expire_minutes" {
  description = "JWT_ACCESS_TOKEN_EXPIRE_MINUTES."
  type        = number
}

variable "jwt_refresh_token_expire_minutes" {
  description = "JWT_REFRESH_TOKEN_EXPIRE_MINUTES."
  type        = number
}
