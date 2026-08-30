# ADR-0009 — No secret value in Terraform; secrets are created empty

## Status

**Accepted** — ANV-40. Recorded in ANV-39. Nothing here has ever been applied.

## Context

`backend/infra/` describes the eventual AWS target: a VPC, RDS, ElastiCache, S3, ECR, three
ECS Fargate services behind an ALB, and Secrets Manager. The application needs six secret
values to run — the Postgres password, the JWT signing key, two vendor API keys and an S3
credential pair.

Terraform has resource types for putting a value into each of those. All of them are
disqualified by the same fact: **`terraform apply` writes every resource attribute into the
state file, in plaintext.** `aws_secretsmanager_secret_version`, `aws_iam_access_key` and
`aws_ssm_parameter` each end with the secret sitting in state. `sensitive = true` does not
help — it marks an **output**, so the value is hidden from the console and still present in
state.

This repository is public, which raises the stakes but is not the argument. The argument is
that a state file is a backup, an S3 object, a thing a colleague downloads to run a plan, and
a thing that ends up in a bucket somebody later makes readable.

## Decision

**No secret value appears anywhere in the Terraform.**

- `backend/infra/modules/secrets` creates **named, empty** Secrets Manager secrets. A human
  fills each one in, once, through the console or the CLI.
- RDS generates its own master password via `manage_master_user_password`; Terraform learns
  only the ARN.
- The three resource types above are **banned**, and
  `backend/tests/unit/test_infra_terraform.py` parses the configuration with a real HCL2
  parser and fails if one appears.
- Nothing account-specific is written down either: the account comes from
  `data.aws_caller_identity`, the partition from `data.aws_partition` (so an ARN is
  `arn:${var.partition}:…`, never `arn:aws:…`), and the region from a variable with no
  default.

## Consequences

**An ECS task will not start until a human has put a value in every box.** That is the
stated cost and it is the correct failure — a task that starts against an empty secret is an
API signing tokens with a blank key. `docs/aws-deployment.md` carries the fill-in step
explicitly, between `terraform apply` and the first deploy, rather than leaving it to be
discovered from a crash loop.

**There is no single command that produces a working environment.** The deploy path is
apply → fill secrets → push an image → force a new deployment. Someone expecting
infrastructure-as-code to mean "one command" will find that surprising; the trade is
reproducibility for safety, and it is only defensible because the estate is small enough
that the manual step is six values, once.

**Verification is credential-free, and that is a design constraint rather than a
convenience.** `backend "s3" {}` is a *partial* backend, so
`terraform init -backend=false && terraform validate && terraform fmt -check -recursive` is
the whole check and no real state bucket is named in a public repository. Nothing installs
Terraform, no script calls it, and no workflow runs it — `apply`, `destroy`, `plan` and
`import` appear in no script and no workflow, because a plan reaches a real account and a
workflow that can run one holds a credential that could do more. A deploy would need
`permissions: id-token: write` on its own job in a separate workflow file, which is then a
decision somebody makes on purpose.

**One consequence propagated into the application and could not be fixed here.** ANV-40
changed no application code, so the fact that `S3Client` refuses a blank key pair — on the
good grounds that botocore would otherwise fall back to the ambient credential chain —
means the deployment must hand it **static credentials**. So `modules/storage` creates an
IAM *user* rather than granting the Fargate task role, which is worse practice than the task
role would be, and the task role's S3 statement is already written out in a comment awaiting
`TODO(ANV-s3-aws)`. The day that TODO is closed, the IAM user, its access key and the
`s3-credentials` secret are all deleted.

**Every test in that directory compares two artefacts, so a value that appears in only one
place is checked by nothing.** Two defects found in review make the point: the ALB's
`enable_deletion_protection` was hardcoded `false` under a comment reading "`false` in
`local`, `true` in `dev`" — the comment was the intent and the code was not it — and the RDS
parameter group had a fixed `name` alongside `create_before_destroy`, which would have
collided with itself during the first major-version replacement. Neither is drift between
two files, so neither was visible to the suite or to `terraform validate`. Only reading the
resource finds that class of defect.
