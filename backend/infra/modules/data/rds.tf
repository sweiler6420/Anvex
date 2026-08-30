/**
 * RDS Postgres — the AWS half of docker-compose's `db` service.
 *
 * **The master password is never in this repository, in this configuration, or in Terraform
 * state.** `manage_master_user_password = true` makes RDS itself generate the password and
 * store it in a Secrets Manager secret it owns and rotates; Terraform only ever learns the
 * secret's ARN. That ARN is what the ECS task definitions hand to the container as
 * `POSTGRES_PASSWORD`, via the `:password::` JSON-key suffix ECS understands.
 *
 * The alternative — `random_password` plus `password = ...` — writes the plaintext into the
 * state file, which is exactly the thing a public repository and an S3 state bucket make
 * expensive to be casual about.
 *
 * Two deliberate omissions:
 *
 *   * **No `rds.force_ssl` parameter.** It would be the right default, and the application
 *     is not ready for it: `Settings.postgres_dsn` builds a plain `postgresql+asyncpg://`
 *     URL with no `ssl` argument, so turning it on here would refuse every connection the
 *     app makes. Enabling TLS is an application change plus this parameter, in one ticket.
 *   * **No Performance Insights and no enhanced monitoring.** Both are billed, and neither
 *     is worth paying for before anything is deployed.
 */

resource "aws_db_subnet_group" "this" {
  name        = var.name
  description = "Data-tier subnets for ${var.name}."
  subnet_ids  = var.subnet_ids
}

# A parameter group of our own even though it currently sets nothing beyond the defaults:
# the default group cannot be modified, so the first parameter anyone ever needs would
# otherwise require replacing the instance. Creating it now costs nothing.
resource "aws_db_parameter_group" "this" {
  # `name_prefix`, not `name`, and the two lines below are why: `family` forces replacement
  # (it is the engine major version), and `create_before_destroy` means the replacement is
  # built while the original still exists. With a fixed name that is a name collision and
  # the apply fails halfway through a major upgrade — the worst possible moment.
  name_prefix = "${var.name}-"
  family      = var.postgres_parameter_group_family
  description = "Postgres parameters for ${var.name}."

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_db_instance" "this" {
  identifier = var.name

  engine         = "postgres"
  engine_version = var.postgres_engine_version
  instance_class = var.postgres_instance_class
  port           = var.postgres_port

  db_name  = var.postgres_db
  username = var.postgres_user

  # See the header. RDS owns the password; Terraform never sees it.
  manage_master_user_password = true

  # gp3 rather than gp2: same price per GiB, and 3,000 IOPS are included at any size instead
  # of being derived from it. A 20 GiB gp2 volume gets 100 IOPS.
  storage_type          = "gp3"
  allocated_storage     = var.allocated_storage
  max_allocated_storage = var.max_allocated_storage
  storage_encrypted     = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  parameter_group_name   = aws_db_parameter_group.this.name
  vpc_security_group_ids = [var.db_security_group_id]
  publicly_accessible    = false

  multi_az                = var.multi_az
  backup_retention_period = var.backup_retention_days
  copy_tags_to_snapshot   = true

  # UTC. Backups run before the maintenance window so a failed patch has a snapshot taken
  # the same night to roll back to, and both are outside US market hours because that is
  # when the ingest jobs run.
  backup_window      = "07:00-08:00"
  maintenance_window = "Sun:08:30-Sun:09:30"

  auto_minor_version_upgrade = true
  apply_immediately          = false

  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = var.skip_final_snapshot
  final_snapshot_identifier = var.skip_final_snapshot ? null : "${var.name}-final"

  # Slow queries and errors, where CloudWatch can see them. `upgrade` logs are how a failed
  # major-version upgrade explains itself, and they exist nowhere else afterwards.
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
}
