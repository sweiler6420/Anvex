variable "name" {
  description = "Name prefix (`anvex-dev`)."
  type        = string
}

variable "subnet_ids" {
  description = "Data-tier subnets. Both subnet groups are built from these."
  type        = list(string)
}

variable "db_security_group_id" {
  description = "Security group for the RDS instance."
  type        = string
}

variable "cache_security_group_id" {
  description = "Security group for the ElastiCache replication group."
  type        = string
}

variable "postgres_engine_version" {
  description = "RDS engine version."
  type        = string
}

variable "postgres_parameter_group_family" {
  description = "Parameter group family, i.e. the engine major version."
  type        = string
}

variable "postgres_instance_class" {
  description = "RDS instance class."
  type        = string
}

variable "postgres_port" {
  description = "Port RDS listens on."
  type        = number
}

variable "postgres_user" {
  description = "Master username."
  type        = string
}

variable "postgres_db" {
  description = "Initial database name."
  type        = string
}

variable "allocated_storage" {
  description = "Initial gp3 storage, in GiB."
  type        = number
}

variable "max_allocated_storage" {
  description = "Storage autoscaling ceiling, in GiB."
  type        = number
}

variable "backup_retention_days" {
  description = "Automated backup retention, in days."
  type        = number
}

variable "multi_az" {
  description = "Run a synchronous standby in a second AZ."
  type        = bool
}

variable "deletion_protection" {
  description = "Refuse to delete the instance."
  type        = bool
}

variable "skip_final_snapshot" {
  description = "Skip the final snapshot on delete."
  type        = bool
}

variable "redis_engine_version" {
  description = "ElastiCache engine version."
  type        = string
}

variable "redis_parameter_group_name" {
  description = "ElastiCache parameter group."
  type        = string
}

variable "redis_node_type" {
  description = "ElastiCache node type."
  type        = string
}

variable "redis_num_cache_nodes" {
  description = "Nodes in the replication group."
  type        = number
}

variable "redis_port" {
  description = "Port ElastiCache listens on."
  type        = number
}
