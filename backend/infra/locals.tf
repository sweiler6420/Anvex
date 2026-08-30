/**
 * Values derived once and shared by every module call.
 */

locals {
  # `anvex-dev`. Every resource name in the estate starts with this, which is what makes
  # two environments in one account safe and what makes `terraform destroy` reviewable.
  name = "${var.project}-${var.environment}"

  # Ports. Fixed by the application, not by a deployment: the Dockerfile `EXPOSE`s 8000 and
  # its `CMD` binds it, and the two data services run on their standard ports. They are
  # named here rather than repeated in four modules.
  api_port      = 8000
  postgres_port = 5432
  redis_port    = 6379

  # The key prefix the S3 lifecycle rule filters on. This is **not** a free choice: it is
  # `app.domain.storage.EXPORTS_PREFIX` with the trailing slash that module's
  # `export_prefix_for_owner` documents, and that module's own docstring says the value
  # "appears in every lifecycle policy". `test_infra_terraform.py` asserts the two agree, so
  # renaming the prefix in Python fails here rather than silently stopping the rule matching.
  s3_export_prefix = "exports/"

  # Applied to everything by the provider's `default_tags`, so individual resources carry
  # no `tags` block unless they need something extra.
  tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
    Repository  = "sweiler6420/Anvex"
  }
}
