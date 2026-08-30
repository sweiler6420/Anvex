/**
 * **The environment contract.** This is the file to read first.
 *
 * `app/settings.py` is the only module in the backend allowed to look at the environment
 * (`CLAUDE.md` §4), so the set of variables it declares *is* the contract between the
 * application and any deployment of it. `local.container_environment` and
 * `local.container_secrets` below are that set, split by whether the value is safe to read
 * in a task definition, and **their union is exactly `Settings`' fields, upper-cased**.
 *
 * `backend/tests/unit/test_infra_terraform.py` asserts that equality in both directions —
 * a field added to `Settings` with no home here fails, and a name here that `Settings` does
 * not read fails. It is the same shape as ANV-43's client/server password-rule drift test
 * and it exists for the same reason: the two halves are edited months apart by people who
 * are not looking at each other's file.
 *
 * Everything below is shared by all three services, because all three are the same image
 * with different commands (see `modules/registry`). `beat` does not open a database
 * connection and `worker` does not serve HTTP, but a per-service environment would mean
 * three contracts to keep in step instead of one, for the sake of omitting four strings.
 *
 * ---------------------------------------------------------------------------------------
 * TODO(ANV-s3-aws): `S3_ENDPOINT_URL` is set to the empty string here and that is **not yet
 * a working value.** `Settings.s3_endpoint_url` defaults to `http://minio:9000`, so it
 * cannot simply be omitted; and `S3Client` passes whatever it holds straight to
 * `aioboto3.client(endpoint_url=...)`, where `""` is not the same as `None`. Making the S3
 * tier work against real AWS is a small application change — let an empty value mean
 * `None` so botocore resolves the regional endpoint itself — and it is deliberately not
 * made here, because ANV-40 changes no application code. The empty string is written down
 * because it is what the operator *means*; a test asserts this marker is still present, so
 * it disappears when the application change lands and not before.
 * ---------------------------------------------------------------------------------------
 */

locals {
  # --------------------------------------------------------------- plain environment
  #
  # Values a `describe-task-definition` may show anybody. Ordered to match the sections of
  # `.env.example`, which is the other half of the same contract.
  container_environment = {
    ANVEX_ENV = var.environment
    LOG_LEVEL = var.log_level

    # `POSTGRES_HOST` is the whole of pointing the app at RDS. ANV-37's `scripts/_common`
    # translation stands aside as soon as this is set, and there is still exactly one DSN,
    # built by `Settings.postgres_dsn` from these five.
    POSTGRES_HOST   = var.postgres_host
    POSTGRES_PORT   = tostring(var.postgres_port)
    POSTGRES_USER   = var.postgres_user
    POSTGRES_DB     = var.postgres_db
    POSTGRES_SCHEMA = var.postgres_schema

    # The broker URLs name logical databases 0 and 1 of the same server, which is why
    # `modules/data` keeps cluster mode off.
    REDIS_HOST            = var.redis_host
    REDIS_PORT            = tostring(var.redis_port)
    CELERY_BROKER_URL     = "redis://${var.redis_host}:${var.redis_port}/0"
    CELERY_RESULT_BACKEND = "redis://${var.redis_host}:${var.redis_port}/1"

    S3_ENDPOINT_URL = "" # TODO(ANV-s3-aws) — see the header.
    S3_REGION       = var.region
    S3_BUCKET       = var.s3_bucket

    JWT_ALGORITHM                    = var.jwt_algorithm
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES  = tostring(var.jwt_access_token_expire_minutes)
    JWT_REFRESH_TOKEN_EXPIRE_MINUTES = tostring(var.jwt_refresh_token_expire_minutes)

    # `Settings.cors_origins` splits on commas and strips, so the list is joined here.
    API_HOST         = "0.0.0.0"
    API_PORT         = tostring(var.api_port)
    API_CORS_ORIGINS = join(",", var.api_cors_origins)
  }

  # ------------------------------------------------------------------------- secrets
  #
  # `name => valueFrom`. ECS resolves each of these at task start, through the execution
  # role, and injects it as an environment variable the same way as the block above — the
  # container cannot tell the difference and neither can `Settings`.
  #
  # The `:key::` suffix is how ECS addresses one field of a JSON secret. The two empty
  # trailing segments are the version stage and version id; omitting them means "current".
  container_secrets = {
    # RDS generated this one and rotates it. See `modules/data/rds.tf`.
    POSTGRES_PASSWORD = "${var.postgres_master_secret_arn}:password::"

    # Plaintext secrets: the ARN alone, no suffix.
    JWT_SECRET_KEY       = var.secret_arns["jwt-signing-key"]
    ALPHAVANTAGE_API_KEY = var.secret_arns["alphavantage"]
    NEWSAPI_API_KEY      = var.secret_arns["newsapi"]

    # One JSON secret, two keys — a key pair is one credential and rotating half of it is
    # never right.
    S3_ACCESS_KEY_ID     = "${var.secret_arns["s3-credentials"]}:access_key_id::"
    S3_SECRET_ACCESS_KEY = "${var.secret_arns["s3-credentials"]}:secret_access_key::"
  }

  # ------------------------------------------------------------------- container shape

  image = "${var.image_repository_url}:${var.image_tag}"

  # The `environment` and `secrets` arrays every container definition shares, in the
  # `[{name, value}]` shape ECS wants. Sorted so a task definition revision does not churn
  # on map ordering.
  environment_entries = [
    for key in sort(keys(local.container_environment)) :
    { name = key, value = local.container_environment[key] }
  ]

  secret_entries = [
    for key in sort(keys(local.container_secrets)) :
    { name = key, valueFrom = local.container_secrets[key] }
  ]

  # The three services, and the one thing that actually differs between them.
  #
  # `api` runs the image's own CMD (`uvicorn app.main:app --host 0.0.0.0 --port 8000`), so
  # its command is null. The other two spell out the same command lines `docker-compose.yml`
  # gives `worker` and `beat`, including `--schedule /tmp/…`: beat's shelve file defaults to
  # the working directory, and `/app` in this image is read-mostly and container-local.
  service_commands = {
    api = null

    worker = [
      "celery", "-A", "app.jobs.celery_app:celery_app", "worker",
      "--pool", "prefork",
      "--concurrency", tostring(var.worker_concurrency),
      "--loglevel", "INFO",
    ]

    beat = [
      "celery", "-A", "app.jobs.celery_app:celery_app", "beat",
      "--schedule", "/tmp/anvex-celerybeat-schedule",
      "--loglevel", "INFO",
    ]
  }

  service_sizes = {
    api    = { cpu = var.api_cpu, memory = var.api_memory }
    worker = { cpu = var.worker_cpu, memory = var.worker_memory }
    beat   = { cpu = var.beat_cpu, memory = var.beat_memory }
  }
}
