/**
 * Terraform and provider versions, and the state backend (ANV-40).
 *
 * The `backend "s3" {}` block is deliberately **empty**. A partial configuration means the
 * bucket, key, region and lock table are supplied at init time
 * (`-backend-config=envs/<env>.s3.tfbackend`), so this repository — which is public — never
 * carries the name of a real state bucket, and `terraform init -backend=false` works with
 * no AWS credentials at all. That second property is what makes this directory reviewable
 * in CI and on a developer's machine: see `README.md`.
 *
 * `required_version` has an upper bound on purpose. A 2.x Terraform is free to change the
 * language; pinning the major means an unattended upgrade fails loudly at `init` rather
 * than silently reinterpreting a resource.
 */

terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }

  backend "s3" {}
}
