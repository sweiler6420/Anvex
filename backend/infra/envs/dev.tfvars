# `dev` — the first environment that would actually be stood up.
#
# Nothing is provisioned from this file today. It is the shape a shared development
# environment takes: survivable rather than durable. The differences from `local.tfvars` are
# the ones worth arguing about, and each is a line in the cost table at
# `docs/aws-deployment.md`:
#
#   api_desired_count 1 -> 2        a deployment with one task has a gap; two do not
#   backup_retention  0 -> 7        seven days of PITR is the cheapest real safety net
#   deletion_protection -> true     and `skip_final_snapshot` -> false
#   log_retention     7 -> 30       a bug reported on Monday about Friday is still findable
#   instance sizes    small -> small-but-not-the-floor
#
# `single_nat_gateway` stays `true` even here. Two NAT gateways is roughly $33/month for AZ
# independence in an environment whose whole point is that it can be down for an hour.

aws_region  = "us-east-1"
environment = "dev"

# --------------------------------------------------------------------------- network
vpc_cidr                = "10.41.0.0/16" # Distinct from `local`, so the two could peer.
availability_zone_count = 2
single_nat_gateway      = true

# -------------------------------------------------------------------------- postgres
postgres_engine_version         = "16.4"
postgres_parameter_group_family = "postgres16"
postgres_instance_class         = "db.t4g.small" # 2 GiB. t4g.micro OOMs under a real seed.
postgres_allocated_storage      = 20
postgres_max_allocated_storage  = 100 # Autoscale rather than page somebody at 2am.
postgres_backup_retention_days  = 7
postgres_multi_az               = false # Doubles the instance cost. Not for dev.
postgres_deletion_protection    = true
postgres_skip_final_snapshot    = false

# ----------------------------------------------------------------------------- redis
redis_engine_version       = "7.1"
redis_parameter_group_name = "default.redis7"
redis_node_type            = "cache.t4g.micro"
redis_num_cache_nodes      = 1

# --------------------------------------------------------------------------- storage
s3_force_destroy          = false
s3_export_expiration_days = 30 # = `app.domain.storage.EXPORT_RETENTION`.

# -------------------------------------------------------------------------- registry
ecr_image_retention_count = 20
# Overridden per deploy with `-var image_tag=<commit sha>`. A rolling `latest` is what a
# rollback cannot undo.
image_tag = "latest"

# --------------------------------------------------------------------------- compute
api_cpu              = 512 # 0.5 vCPU
api_memory           = 1024
api_desired_count    = 2 # The minimum that survives its own deployment.
worker_cpu           = 512
worker_memory        = 1024
worker_desired_count = 1
worker_concurrency   = 4 # Four prefork slots is four Postgres connections.
beat_cpu             = 256
beat_memory          = 512
cpu_architecture     = "X86_64"

alb_deletion_protection   = true # Deleting this deletes the public surface. Do it on purpose.
enable_container_insights = false
log_retention_days        = 30

# ----------------------------------------------------------------------- application
log_level = "INFO"
# There is no frontend origin yet — the SPA is not deployed by this configuration. Until
# there is one, only the local dev server is allowed, which is also what makes it obvious
# that this list is the thing to change.
api_cors_origins                 = ["http://localhost:5173"]
jwt_access_token_expire_minutes  = 30
jwt_refresh_token_expire_minutes = 10080

secret_recovery_window_days = 7
