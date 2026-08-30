/**
 * Secrets Manager — **containers only. No values, ever.**
 *
 * Every secret here is an `aws_secretsmanager_secret` with no `aws_secretsmanager_secret_version`
 * beside it, and that omission is the whole design:
 *
 *   * A `secret_version` puts the plaintext into the Terraform **state file**. State lives in
 *     an S3 bucket that more people can read than can read a secret, and `terraform show`
 *     prints it. "Sensitive" marks the *output*, not the state.
 *   * This repository is **public**. A default, an example, or a placeholder that looks like
 *     a key is a mistake somebody eventually pastes over with a real one.
 *
 * So Terraform creates the named box and the IAM grant that lets exactly the ECS execution
 * role open it, and a human puts the value in once with `aws secretsmanager put-secret-value`.
 * `docs/aws-deployment.md` has the commands. `test_infra_terraform.py` fails if an
 * `aws_secretsmanager_secret_version` resource ever appears in this tree.
 *
 * **The consequence is real and worth stating plainly: an ECS task will not start until
 * every secret named here has a value.** It fails at `ResourceInitializationError`, before
 * the container runs. That is the correct failure — the alternative is an API running with
 * a JWT signing key somebody committed.
 *
 * The Postgres password is deliberately *not* here. RDS generates and owns it
 * (`manage_master_user_password`); see `modules/data/rds.tf`.
 */

locals {
  # `name => description`. The name is the suffix; the full secret name is prefixed, so two
  # environments in one account do not collide.
  #
  # Each of these becomes a container `secrets` entry in `modules/compute`, and the mapping
  # from secret to environment variable lives there, in one place.
  definitions = {
    "jwt-signing-key" = "HS256 signing key for access and refresh tokens (JWT_SECRET_KEY). Plaintext."
    "alphavantage"    = "AlphaVantage API key (ALPHAVANTAGE_API_KEY). Plaintext."
    "newsapi"         = "NewsAPI key (NEWSAPI_API_KEY). Plaintext."
    "s3-credentials"  = "JSON: {\"access_key_id\": ..., \"secret_access_key\": ...} for the app's IAM user. See modules/storage/iam.tf."
  }
}

resource "aws_secretsmanager_secret" "this" {
  for_each = local.definitions

  name        = "${var.name}/${each.key}"
  description = each.value

  recovery_window_in_days = var.recovery_window_days
}
