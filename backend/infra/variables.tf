/**
 * Root input variables (ANV-40).
 *
 * The layout is `local` / `dev`: two files under `envs/`, one root module, one set of
 * variables. `local` is **not a deployment** — it is the variable set that mirrors
 * `docker-compose.yml` one for one (single AZ, one task per service, the smallest instance
 * classes AWS sells) and exists so the cost document has an honest floor and so a reviewer
 * can diff "what we run locally" against "what we would pay for". Local development reads
 * none of this; the compose stack is and stays the local story.
 *
 * Rules this file follows, and that `test_infra_terraform.py` enforces:
 *
 *   * Every variable declared here is referenced somewhere in the configuration, and every
 *     `var.x` referenced is declared here. An orphan variable is a knob that turns nothing.
 *   * `aws_region` and `environment` have **no default**. Where you are deploying and which
 *     environment you are is never a sensible thing to guess.
 *   * No default anywhere is a region, an account id, an ARN or a credential.
 */

# --------------------------------------------------------------------------- identity

variable "aws_region" {
  description = "Region every resource is created in. No default: `envs/<env>.tfvars` must say."
  type        = string
}

variable "environment" {
  description = "Environment name. Becomes part of every resource name and of ANVEX_ENV."
  type        = string

  validation {
    # The two the variable layout defines. Adding a third means adding its tfvars file, and
    # making that a deliberate act is the point.
    condition     = contains(["local", "dev"], var.environment)
    error_message = "environment must be one of: local, dev."
  }
}

variable "project" {
  description = "Name prefix for every resource. Changing it renames the entire estate."
  type        = string
  default     = "anvex"
}

# ---------------------------------------------------------------------------- network

variable "vpc_cidr" {
  description = "CIDR block for the VPC. Must be large enough for three /20s per tier."
  type        = string
}

variable "availability_zone_count" {
  description = "How many AZs to spread the subnets across. RDS and the ALB both need >= 2."
  type        = number

  validation {
    condition     = var.availability_zone_count >= 2 && var.availability_zone_count <= 3
    error_message = "availability_zone_count must be 2 or 3: an ALB requires two subnets, and beyond three the NAT bill grows faster than the availability does."
  }
}

variable "single_nat_gateway" {
  description = <<-EOT
    Route every private subnet through one NAT gateway instead of one per AZ.

    This is the single largest cost lever in the whole configuration — a NAT gateway is
    charged per hour *and* per gigabyte, and it is the most expensive line in the estimate
    at `docs/aws-deployment.md`. `true` trades an AZ-independent egress path for roughly
    two thirds of that bill, which is the right trade for a non-production environment and
    the wrong one for production.
  EOT
  type        = bool
}

variable "enable_s3_gateway_endpoint" {
  description = <<-EOT
    Create the S3 gateway VPC endpoint.

    Gateway endpoints are free, and every export the app writes plus every image layer ECR
    serves from S3 would otherwise cross the NAT gateway at per-gigabyte rates. There is no
    reason to turn this off; the variable exists so the reason is written down.
  EOT
  type        = bool
  default     = true
}

# --------------------------------------------------------------------------- postgres

variable "postgres_engine_version" {
  description = "RDS engine version. Track `postgres:16-alpine` in docker-compose.yml."
  type        = string
}

variable "postgres_parameter_group_family" {
  description = "Parameter group family, which is the engine's major version (`postgres16`)."
  type        = string
}

variable "postgres_instance_class" {
  description = "RDS instance class. Graviton (`db.t4g.*`) is cheaper than `db.t3.*` for the same size."
  type        = string
}

variable "postgres_allocated_storage" {
  description = "Initial gp3 storage, in GiB."
  type        = number
}

variable "postgres_max_allocated_storage" {
  description = "Autoscaling ceiling for storage, in GiB. Equal to the initial size disables autoscaling."
  type        = number
}

variable "postgres_backup_retention_days" {
  description = "Automated backup retention. 0 disables backups entirely, which also disables PITR."
  type        = number
}

variable "postgres_multi_az" {
  description = "Run a synchronous standby in a second AZ. Doubles the instance cost."
  type        = bool
}

variable "postgres_deletion_protection" {
  description = "Refuse `terraform destroy` on the database."
  type        = bool
}

variable "postgres_skip_final_snapshot" {
  description = "Skip the final snapshot on delete. `true` is only ever right for a throwaway environment."
  type        = bool
}

variable "postgres_user" {
  description = "Master username. Mirrors POSTGRES_USER in `.env.example`. The password is never set here — see `modules/data/rds.tf`."
  type        = string
  default     = "anvex"
}

variable "postgres_db" {
  description = "Initial database name. Mirrors POSTGRES_DB in `.env.example`."
  type        = string
  default     = "anvex"
}

variable "postgres_schema" {
  description = "Schema the application uses. Mirrors POSTGRES_SCHEMA; created by alembic, not by RDS."
  type        = string
  default     = "anvex"
}

# ------------------------------------------------------------------------------ redis

variable "redis_engine_version" {
  description = "ElastiCache engine version. Track `redis:7-alpine` in docker-compose.yml."
  type        = string
}

variable "redis_parameter_group_name" {
  description = "ElastiCache parameter group. AWS's `default.redis7` is correct for the stock configuration."
  type        = string
}

variable "redis_node_type" {
  description = "ElastiCache node type. Celery's broker footprint here is tiny."
  type        = string
}

variable "redis_num_cache_nodes" {
  description = "Nodes in the replication group. 1 means no failover; >1 enables automatic failover and multi-AZ."
  type        = number

  validation {
    condition     = var.redis_num_cache_nodes >= 1 && var.redis_num_cache_nodes <= 3
    error_message = "redis_num_cache_nodes must be between 1 and 3."
  }
}

# --------------------------------------------------------------------------- storage

variable "s3_force_destroy" {
  description = "Allow `terraform destroy` to empty the exports bucket first. Never enable outside a throwaway environment."
  type        = bool
}

variable "s3_export_expiration_days" {
  description = "Days before an object under the exports prefix is expired. 0 disables the lifecycle rule."
  type        = number
}

# -------------------------------------------------------------------------- registry

variable "ecr_image_retention_count" {
  description = "How many tagged images ECR keeps before the lifecycle policy expires the oldest."
  type        = number
}

variable "image_tag" {
  description = <<-EOT
    Tag of the image all three services run.

    One tag, because `docker-compose.yml` runs one image: `api`, `worker` and `beat` are the
    same `anvex/api` build with different commands. That is why `modules/registry` creates a
    single repository and not three.
  EOT
  type        = string
}

# ---------------------------------------------------------------------------- compute

variable "api_cpu" {
  description = "Fargate CPU units for the API task (1024 = 1 vCPU)."
  type        = number
}

variable "api_memory" {
  description = "Fargate memory for the API task, in MiB. Must be a legal pairing with api_cpu."
  type        = number
}

variable "api_desired_count" {
  description = "How many API tasks to run. 2 is the minimum that survives a deployment without a gap."
  type        = number
}

variable "worker_cpu" {
  description = "Fargate CPU units for the Celery worker task."
  type        = number
}

variable "worker_memory" {
  description = "Fargate memory for the Celery worker task, in MiB."
  type        = number
}

variable "worker_desired_count" {
  description = "How many Celery worker tasks to run."
  type        = number
}

variable "worker_concurrency" {
  description = "`--concurrency` for the prefork worker pool. Each slot is a Postgres connection."
  type        = number
}

variable "beat_cpu" {
  description = "Fargate CPU units for the Celery beat task. It publishes messages on a timer and nothing else."
  type        = number
}

variable "beat_memory" {
  description = "Fargate memory for the Celery beat task, in MiB."
  type        = number
}

variable "cpu_architecture" {
  description = <<-EOT
    Fargate CPU architecture, `X86_64` or `ARM64`.

    `ARM64` is roughly 20% cheaper for identical CPU/memory and the base image
    (`python:3.12-slim-bookworm`) is multi-arch, so it works — but every push then has to be
    `docker buildx build --platform linux/arm64`, and an x86 image pushed to an ARM64 task
    definition fails at task start with an exec-format error rather than at deploy time.
    Both `envs/*.tfvars` therefore say `X86_64`; see `docs/aws-deployment.md` for the saving.
  EOT
  type        = string

  validation {
    condition     = contains(["X86_64", "ARM64"], var.cpu_architecture)
    error_message = "cpu_architecture must be X86_64 or ARM64."
  }
}

variable "alb_deletion_protection" {
  description = "Refuse `terraform destroy` on the load balancer. The public surface's equivalent of `postgres_deletion_protection`."
  type        = bool
}

variable "enable_container_insights" {
  description = "ECS Container Insights. Real per-metric CloudWatch charges, so it is off by default."
  type        = bool
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the three service log groups. `0` means never expire."
  type        = number
}

variable "acm_certificate_arn" {
  description = <<-EOT
    ACM certificate for the HTTPS listener, or `null` for an HTTP-only listener.

    Null by default and null in both tfvars files: a certificate belongs to a domain that
    does not exist yet, and a public repository is not the place to write one down. With a
    value, port 80 becomes a permanent redirect to 443 instead of a forward.
  EOT
  type        = string
  default     = null
}

# ------------------------------------------------------------------------ application

variable "log_level" {
  description = "LOG_LEVEL for all three services."
  type        = string
}

variable "api_cors_origins" {
  description = "Origins allowed by the API. Joined with commas into API_CORS_ORIGINS, which is what `Settings.cors_origins` splits."
  type        = list(string)
}

variable "jwt_access_token_expire_minutes" {
  description = "JWT_ACCESS_TOKEN_EXPIRE_MINUTES."
  type        = number
}

variable "jwt_refresh_token_expire_minutes" {
  description = "JWT_REFRESH_TOKEN_EXPIRE_MINUTES."
  type        = number
}

variable "jwt_algorithm" {
  description = "JWT_ALGORITHM. HS256 is symmetric, which is why JWT_SECRET_KEY is a Secrets Manager secret."
  type        = string
  default     = "HS256"
}

variable "secret_recovery_window_days" {
  description = "Secrets Manager recovery window on delete. 0 deletes immediately, which is only right for a throwaway environment."
  type        = number
}
