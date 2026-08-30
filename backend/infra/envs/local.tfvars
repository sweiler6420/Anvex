# `local` — the variable set that mirrors `docker-compose.yml`, one for one.
#
# **This is not a deployment.** Nothing in `Anvex` runs against it and nothing ever should:
# local development is `docker compose up`, and `CLAUDE.md` §1 says so. It exists for two
# reasons, both about reading rather than running:
#
#   * It is the honest **floor** of the cost estimate in `docs/aws-deployment.md`. The
#     cheapest shape this application can take in AWS is the shape it already has locally —
#     one of everything, no redundancy — and that number is what tells you whether the
#     answer is "deploy it" or "keep using compose".
#   * It makes the compose stack and the Terraform diffable. `db: postgres:16-alpine` is
#     `postgres_engine_version = "16.4"`; `redis:7-alpine` is `redis_engine_version = "7.1"`;
#     `--concurrency 2` is `worker_concurrency = 2`. Where a line here does not correspond to
#     a line there, that is a decision somebody should be able to point at.
#
# Every durability setting is off, because a throwaway environment that is expensive to
# throw away is not throwaway.

aws_region  = "us-east-1"
environment = "local"

# --------------------------------------------------------------------------- network
vpc_cidr                = "10.40.0.0/16"
availability_zone_count = 2    # The floor: an ALB needs two subnets and RDS needs two AZs.
single_nat_gateway      = true # The largest single saving available. See variables.tf.

# -------------------------------------------------------------------------- postgres
# compose: `image: postgres:16-alpine`
postgres_engine_version         = "16.4"
postgres_parameter_group_family = "postgres16"
postgres_instance_class         = "db.t4g.micro" # 2 vCPU burst, 1 GiB. Graviton.
postgres_allocated_storage      = 20             # The RDS minimum.
postgres_max_allocated_storage  = 20             # Equal to the initial size: no autoscaling.
postgres_backup_retention_days  = 0              # No backups, and therefore no PITR.
postgres_multi_az               = false
postgres_deletion_protection    = false
postgres_skip_final_snapshot    = true

# ----------------------------------------------------------------------------- redis
# compose: `image: redis:7-alpine`, `--save "" --appendonly no`
redis_engine_version       = "7.1"
redis_parameter_group_name = "default.redis7"
redis_node_type            = "cache.t4g.micro" # 0.5 GiB. A Celery broker holds very little.
redis_num_cache_nodes      = 1                 # No failover; see modules/data/redis.tf.

# --------------------------------------------------------------------------- storage
s3_force_destroy = true
# 30 days, because `app.domain.storage.EXPORT_RETENTION` is 30 days. A test asserts it.
s3_export_expiration_days = 30

# -------------------------------------------------------------------------- registry
ecr_image_retention_count = 5
image_tag                 = "latest"

# --------------------------------------------------------------------------- compute
# compose runs one of each. So does this.
api_cpu              = 256 # 0.25 vCPU
api_memory           = 512
api_desired_count    = 1 # A deployment therefore has a gap. Fine here, not fine in dev.
worker_cpu           = 256
worker_memory        = 512
worker_desired_count = 1
worker_concurrency   = 2 # compose: `--concurrency 2`
beat_cpu             = 256
beat_memory          = 512
# X86_64 rather than ARM64: ARM is ~20% cheaper but every push then needs `buildx
# --platform linux/arm64`, and the mismatch fails at task start rather than at deploy.
cpu_architecture = "X86_64"

enable_container_insights = false
log_retention_days        = 7

# ----------------------------------------------------------------------- application
log_level = "DEBUG"
# The Vite dev server's origins, which is what `.env.example` already allows.
api_cors_origins                 = ["http://localhost:5173", "http://127.0.0.1:5173"]
jwt_access_token_expire_minutes  = 30
jwt_refresh_token_expire_minutes = 10080

secret_recovery_window_days = 0 # Immediate delete, so a destroy/apply cycle is repeatable.
