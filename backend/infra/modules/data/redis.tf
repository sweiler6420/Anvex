/**
 * ElastiCache Redis — the AWS half of docker-compose's `redis` service.
 *
 * This is **Celery's broker and result backend**, not a cache: `app/jobs/celery_app.py`
 * points `celery_broker_url` at database 0 and `celery_result_backend` at database 1 of the
 * same server. Two consequences that are not obvious from the resource:
 *
 *   * **Cluster mode is off, and must stay off.** `redis://host:6379/0` names a logical
 *     database, and a cluster-mode Redis has exactly one. Turning on sharding would break
 *     the broker URL the application has always used.
 *   * **`transit_encryption_enabled` is false.** Turning it on changes the scheme the client
 *     must use to `rediss://` and requires an auth token, and `Settings.redis_url` builds
 *     `redis://`. Like `rds.force_ssl` next door, that is an application change plus this
 *     flag, in one ticket — not a flag flipped here on its own.
 *
 * `snapshot_retention_limit = 0` is also deliberate. A broker's contents are in-flight
 * messages; restoring yesterday's would re-run yesterday's jobs, and the beat schedule
 * re-drives anything that matters on its next tick anyway. It matches the local service,
 * which runs with `--save "" --appendonly no`.
 */

resource "aws_elasticache_subnet_group" "this" {
  name        = var.name
  description = "Data-tier subnets for ${var.name}."
  subnet_ids  = var.subnet_ids
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = var.name
  description          = "Celery broker and result backend for ${var.name}."

  engine               = "redis"
  engine_version       = var.redis_engine_version
  node_type            = var.redis_node_type
  parameter_group_name = var.redis_parameter_group_name
  port                 = var.redis_port

  num_cache_clusters = var.redis_num_cache_nodes

  # Failover needs somewhere to fail over to. With one node both are impossible, and AWS
  # rejects the combination rather than ignoring it.
  automatic_failover_enabled = var.redis_num_cache_nodes > 1
  multi_az_enabled           = var.redis_num_cache_nodes > 1

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = [var.cache_security_group_id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = false

  # See the header: a snapshot of a broker is a queue of jobs that already ran.
  snapshot_retention_limit = 0

  maintenance_window         = "sun:09:30-sun:10:30"
  auto_minor_version_upgrade = true
  apply_immediately          = false
}
