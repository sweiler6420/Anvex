/**
 * The AWS provider, and the two data sources every module borrows from it.
 *
 * `region` comes from a variable with **no default**: an environment that forgets to say
 * where it lives should fail at plan time, not quietly deploy to whatever `AWS_REGION`
 * happened to be exported. `backend/tests/unit/test_infra_terraform.py` asserts no `.tf`
 * file in this tree contains a literal region name.
 *
 * `default_tags` is why almost no individual resource carries a `tags` block: the provider
 * applies `local.tags` to everything that supports tagging.
 */

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

# The account and partition this is being planned against. Every ARN this configuration
# constructs by hand is built from these rather than written down, which is what keeps a
# real account id out of a public repository.
data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}
