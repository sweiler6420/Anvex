# `backend/infra/` — AWS infrastructure, as configuration only

**Nothing here has been applied.** No AWS account has been touched, no credential exists, no
resource has been created, and the running cost is $0.00. ANV-40 produced reviewable
Terraform and [`docs/aws-deployment.md`](../../docs/aws-deployment.md), which is the deploy
path and the monthly cost of actually standing it up. Read that one for the *why*; this one
is the map.

**Local development does not depend on any of this.** `docker compose up` is the local story
(`CLAUDE.md` §1). `backend/tests/unit/test_infra_terraform.py` asserts that nothing under
`scripts/`, `docker-compose.yml` or `.env.example` even mentions Terraform.

## Verifying it, with no AWS account

```sh
cd backend/infra
terraform init -backend=false     # -backend=false: do not reach for the state bucket
terraform validate
terraform fmt -check -recursive
```

`-backend=false` is not optional. `versions.tf` declares `backend "s3" {}` as a *partial*
configuration — the bucket, key and lock table are supplied at init time from an untracked
`envs/<env>.s3.tfbackend`, so no real bucket name is in this public repository — and a plain
`init` would try to reach it and ask for credentials.

Terraform is not a repository dependency. There is no version of it pinned to a lockfile and
no script that installs it; `versions.tf` requires `>= 1.9.0, < 2.0.0` and any of them will
do. `.terraform.lock.hcl` *is* committed, with checksums for `linux_amd64`, `windows_amd64`
and `darwin_arm64`, so the provider is pinned even though the tool is not.

## Layout

```
infra/
├── versions.tf      terraform + provider versions, the partial S3 backend
├── providers.tf     the aws provider, default_tags, caller identity and partition
├── variables.tf     every input, with the local/dev contract described
├── locals.tf        name prefix, tags, the fixed ports, the S3 export prefix
├── main.tf          six module calls and the wiring between them
├── outputs.tf       what an operator needs after an apply
├── envs/
│   ├── local.tfvars the compose stack's sizing. NOT a deployment - see below.
│   └── dev.tfvars   the first environment that would really be stood up
└── modules/
    ├── network/     VPC, three subnet tiers, NAT, four security groups, S3 endpoint
    ├── data/        RDS Postgres, ElastiCache Redis, their subnet groups
    ├── storage/     the exports bucket, its lifecycle, and the app's IAM user
    ├── registry/    one ECR repository
    ├── secrets/     Secrets Manager secrets - containers only, never values
    └── compute/     ALB, ECS cluster, three task definitions, three services, IAM roles
```

## The five things worth knowing before you edit any of it

**1. `local` is not a deployment.** It is the variable set that mirrors `docker-compose.yml`
one for one — one of everything, no backups, no redundancy. It exists so the cost document
has an honest floor and so the compose stack and the Terraform are diffable. Nothing runs
against it.

**2. One image, three services.** `docker-compose.yml` gives `api`, `worker` and `beat` the
same `anvex/api` image and differs only in the `command`. So `modules/registry` creates **one**
ECR repository and `modules/compute` builds all three task definitions from the same
`"${repository_url}:${image_tag}"`. Three repositories would be three chances for the worker
to run a different commit from the API that enqueued its work.

**3. `modules/compute/locals.tf` is the environment contract, and it is tested.**
`local.container_environment` and `local.container_secrets` together are exactly the fields
`app/settings.py` declares, upper-cased — `app/settings.py` being the only module in the
backend allowed to read the environment (`CLAUDE.md` §4). `test_infra_terraform.py` asserts
that equality in both directions, so a new `Settings` field with no home here fails the
backend suite. It is the same shape as ANV-43's client/server password-rule drift test.

**4. No secret value is ever in Terraform.** `modules/secrets` creates named, empty secrets
and no `aws_secretsmanager_secret_version`; `modules/storage` creates an IAM user and no
`aws_iam_access_key`; RDS generates its own master password and Terraform only learns the ARN.
A `secret_version` would write plaintext into the state file. The consequence is real: **an
ECS task will not start until every secret has a value**, put there by hand — see step 3 of
the deploy path.

**5. `beat` is never scaled.** `desired_count = 1` is a literal, not a variable, and the
service stops its old task before starting the new one
(`deployment_minimum_healthy_percent = 0`). Two schedulers publish every tick twice.
`docker-compose.yml` says the same thing in its own comment.

## What is deliberately not here

- **The frontend.** Serving the SPA is S3 + CloudFront and its own ticket.
- **TLS to Postgres and Redis**, and **real-S3 support**. Both need a small application change
  first; both are argued in `docs/aws-deployment.md` §4.
- **Any automation.** No `scripts/` pair, no CI job, nothing that runs `terraform apply`.
  A deploy workflow needs `permissions: id-token: write` on its own job in a separate file,
  so a deploy credential is never in scope for a test run (ANV-38).
