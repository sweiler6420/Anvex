/**
 * The root module: five module calls and the wiring between them.
 *
 * ```
 *   network ─┬─> data ─────┐
 *            ├─> compute <─┤   (endpoints, the RDS-owned secret)
 *   storage ─┴─> compute <─┤   (bucket name)
 *   registry ───> compute <─┤   (image)
 *   secrets ────> compute <─┘   (secret ARNs)
 * ```
 *
 * Every dependency is expressed by passing an output, never by `depends_on`. Terraform's
 * graph is derived from those references, so the ordering is a consequence of the wiring
 * rather than something stated twice and able to disagree with itself.
 */

module "network" {
  source = "./modules/network"

  name                       = local.name
  vpc_cidr                   = var.vpc_cidr
  availability_zone_count    = var.availability_zone_count
  single_nat_gateway         = var.single_nat_gateway
  enable_s3_gateway_endpoint = var.enable_s3_gateway_endpoint

  api_port      = local.api_port
  postgres_port = local.postgres_port
  redis_port    = local.redis_port
}

module "data" {
  source = "./modules/data"

  name                    = local.name
  subnet_ids              = module.network.data_subnet_ids
  db_security_group_id    = module.network.db_security_group_id
  cache_security_group_id = module.network.cache_security_group_id

  postgres_engine_version         = var.postgres_engine_version
  postgres_parameter_group_family = var.postgres_parameter_group_family
  postgres_instance_class         = var.postgres_instance_class
  postgres_port                   = local.postgres_port
  postgres_user                   = var.postgres_user
  postgres_db                     = var.postgres_db
  allocated_storage               = var.postgres_allocated_storage
  max_allocated_storage           = var.postgres_max_allocated_storage
  backup_retention_days           = var.postgres_backup_retention_days
  multi_az                        = var.postgres_multi_az
  deletion_protection             = var.postgres_deletion_protection
  skip_final_snapshot             = var.postgres_skip_final_snapshot

  redis_engine_version       = var.redis_engine_version
  redis_parameter_group_name = var.redis_parameter_group_name
  redis_node_type            = var.redis_node_type
  redis_num_cache_nodes      = var.redis_num_cache_nodes
  redis_port                 = local.redis_port
}

module "storage" {
  source = "./modules/storage"

  name = local.name

  # Bucket names are global, so the account id is what makes this one ours. Read from the
  # caller's identity rather than written down — see `providers.tf`.
  account_id = data.aws_caller_identity.current.account_id

  force_destroy          = var.s3_force_destroy
  export_expiration_days = var.s3_export_expiration_days
  export_prefix          = local.s3_export_prefix
}

module "registry" {
  source = "./modules/registry"

  name                  = local.name
  image_retention_count = var.ecr_image_retention_count
}

module "secrets" {
  source = "./modules/secrets"

  name                 = local.name
  recovery_window_days = var.secret_recovery_window_days
}

module "compute" {
  source = "./modules/compute"

  name        = local.name
  environment = var.environment
  partition   = data.aws_partition.current.partition
  region      = var.aws_region

  vpc_id                = module.network.vpc_id
  public_subnet_ids     = module.network.public_subnet_ids
  private_subnet_ids    = module.network.private_subnet_ids
  alb_security_group_id = module.network.alb_security_group_id
  app_security_group_id = module.network.app_security_group_id

  image_repository_url = module.registry.repository_url
  image_repository_arn = module.registry.repository_arn
  image_tag            = var.image_tag
  cpu_architecture     = var.cpu_architecture

  api_cpu              = var.api_cpu
  api_memory           = var.api_memory
  api_desired_count    = var.api_desired_count
  worker_cpu           = var.worker_cpu
  worker_memory        = var.worker_memory
  worker_desired_count = var.worker_desired_count
  worker_concurrency   = var.worker_concurrency
  beat_cpu             = var.beat_cpu
  beat_memory          = var.beat_memory

  api_port                  = local.api_port
  acm_certificate_arn       = var.acm_certificate_arn
  alb_deletion_protection   = var.alb_deletion_protection
  enable_container_insights = var.enable_container_insights
  log_retention_days        = var.log_retention_days
  log_level                 = var.log_level

  # The environment contract. See `modules/compute/locals.tf`.
  postgres_host              = module.data.postgres_host
  postgres_port              = module.data.postgres_port
  postgres_user              = var.postgres_user
  postgres_db                = var.postgres_db
  postgres_schema            = var.postgres_schema
  postgres_master_secret_arn = module.data.postgres_master_secret_arn
  redis_host                 = module.data.redis_host
  redis_port                 = module.data.redis_port
  s3_bucket                  = module.storage.bucket_name
  secret_arns                = module.secrets.arns

  api_cors_origins                 = var.api_cors_origins
  jwt_algorithm                    = var.jwt_algorithm
  jwt_access_token_expire_minutes  = var.jwt_access_token_expire_minutes
  jwt_refresh_token_expire_minutes = var.jwt_refresh_token_expire_minutes
}
