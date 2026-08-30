# AWS deployment — the intended path, and what it would cost

> **Nothing in this document has been provisioned.** `backend/infra/` is Terraform that
> `init`s and `validate`s and has never been applied: no AWS account has been touched, no
> credential exists, no resource has been created and **the running cost today is $0.00**.
> ANV-40 produced reviewable configuration and this page, so that the decision to actually
> stand it up is made with a number in front of it rather than after the first invoice.
>
> **Local development does not depend on any of this and never will.** `docker compose up`
> is the local story (`CLAUDE.md` §1); a test in `backend/tests/unit/test_infra_terraform.py`
> asserts that nothing under `scripts/`, `docker-compose.yml` or `.env.example` so much as
> mentions Terraform.

---

## 1. What the configuration describes

The same seven things `docker-compose.yml` runs, in their AWS shapes. The correspondence is
deliberate and is the point of the ticket — a generic AWS skeleton would tell you nothing
about *this* application.

| `docker-compose.yml` | AWS | Terraform |
| --- | --- | --- |
| `db` (`postgres:16-alpine`) | RDS for PostgreSQL 16 | `modules/data/rds.tf` |
| `redis` (`redis:7-alpine`) | ElastiCache for Redis 7 | `modules/data/redis.tf` |
| `minio` + `minio-init` | S3 bucket + lifecycle | `modules/storage/` |
| `api` | ECS Fargate service behind an ALB | `modules/compute/` |
| `worker` (`--profile celery`) | ECS Fargate service, no load balancer | `modules/compute/` |
| `beat` (`--profile celery`) | ECS Fargate service, `desired_count = 1`, always | `modules/compute/` |
| `image: anvex/api:dev` (all three) | **one** ECR repository | `modules/registry/` |
| the repo-root `.env` | task-definition `environment` + Secrets Manager | `modules/compute/locals.tf` |
| — | VPC, three subnet tiers, NAT, four security groups | `modules/network/` |
| `web` (Vite dev server) | **not deployed by this configuration** | — |

The frontend is out of scope. Serving the SPA is S3 + CloudFront and its own ticket; today
`api_cors_origins` in both tfvars files points at the local dev server, which is also what
makes it obvious that it is the thing to change.

### The two environments

`envs/local.tfvars` and `envs/dev.tfvars` are the variable layout. **`local` is not a
deployment** — it is the sizing that mirrors the compose stack one for one (one of
everything, no backups, no redundancy) and it exists to be the floor of the table below.
`dev` is the first environment that would really be stood up: two API tasks so a deployment
has no gap, seven days of backups, deletion protection on.

---

## 2. The deploy path

Nine steps. Steps 1–3 happen once; 4–9 are every deploy.

```sh
cd backend/infra
```

**1. Initialise, against a state backend that exists.** `versions.tf` declares
`backend "s3" {}` with no arguments on purpose, so no real bucket name is in this public
repository. Create the bucket and the lock table by hand, write an untracked
`envs/dev.s3.tfbackend` naming them, then:

```sh
terraform init -backend-config=envs/dev.s3.tfbackend
```

*Every command in this document that is not read-only is a command a human types.* There is
no `scripts/deploy`, no CI job, and nothing anywhere in this repository runs
`terraform apply` — see §6.

**2. Apply.**

```sh
terraform plan  -var-file=envs/dev.tfvars -out=dev.tfplan
terraform apply dev.tfplan
```

Roughly fifteen minutes, most of it RDS. **The three ECS services will not have a running
task at the end of it, and that is expected** — the secrets in step 3 do not have values
yet, so every task fails at `ResourceInitializationError` before its container starts.

**3. Populate the secrets — the manual step, and the deliberate one.** Terraform creates
each secret as an empty named box and never a value: a `secret_version` resource writes the
plaintext into Terraform state, and state is readable by more people than a secret is. So:

```sh
aws secretsmanager put-secret-value --secret-id anvex-dev/jwt-signing-key \
  --secret-string "$(openssl rand -base64 48)"

aws secretsmanager put-secret-value --secret-id anvex-dev/alphavantage \
  --secret-string '<your AlphaVantage key>'

aws secretsmanager put-secret-value --secret-id anvex-dev/newsapi \
  --secret-string '<your NewsAPI key>'

# The S3 credential. `terraform output app_iam_user_name` names the user; Terraform
# creates no access key, because that too would land in state.
aws iam create-access-key --user-name "$(terraform output -raw app_iam_user_name)"
aws secretsmanager put-secret-value --secret-id anvex-dev/s3-credentials \
  --secret-string '{"access_key_id":"<AccessKeyId>","secret_access_key":"<SecretAccessKey>"}'
```

The Postgres password is **not** in that list. RDS generates and rotates it itself
(`manage_master_user_password`), Terraform only ever learns the ARN, and the task
definitions pull the `password` field out of the RDS-owned secret.

**4. Build and push.** One image; all three services run it.

```sh
REPO=$(terraform output -raw ecr_repository_url)
TAG=$(git rev-parse --short HEAD)

aws ecr get-login-password | docker login --username AWS --password-stdin "${REPO%%/*}"
docker build -t "$REPO:$TAG" backend
docker push "$REPO:$TAG"
```

Tag with the commit, not `latest`: a rolling tag is what a rollback cannot undo. `dev.tfvars`
says `image_tag = "latest"` as a default that a real deploy overrides with
`-var image_tag=$TAG`.

**5. Migrate.** There is no migration container and no bastion. `alembic` runs on a host that
can reach RDS, and the cheapest such host is a task that is already there:

```sh
aws ecs execute-command --cluster anvex-dev --task <api-task-id> --container api \
  --interactive --command "alembic upgrade head"
```

`enable_execute_command` is on for all three services for exactly this. The alternative —
`POSTGRES_HOST=<rds endpoint> ./scripts/migrate.sh` from a laptop — needs the database
reachable from outside the VPC, which it deliberately is not. **Either way there is still one
DSN**: `POSTGRES_HOST` in the environment is the whole of pointing the tooling somewhere else,
and ANV-37's `_common` translation stands aside as soon as it is set.

**6. Roll the services.**

```sh
for service in api worker beat; do
  aws ecs update-service --cluster anvex-dev --service "$service" --force-new-deployment
done
```

`api` and `worker` roll normally. **`beat` stops its old task before starting the new one**
(`deployment_minimum_healthy_percent = 0`), so there is a gap of a few seconds with no
scheduler — which is free, because every scheduled job is idempotent and the next tick
re-drives whatever the gap missed, and the alternative is two schedulers publishing every
tick twice.

**7. Check it.**

```sh
curl "http://$(terraform output -raw alb_dns_name)/health"        # liveness
curl "http://$(terraform output -raw alb_dns_name)/health/ready"  # readiness: SELECT 1
```

`http`, not `https`: there is no domain and therefore no certificate. Set
`acm_certificate_arn` and port 80 becomes a 301 to 443. **Do not put anything real behind
this until you have.**

**8. Watch the logs.** `terraform output log_group_names` gives one CloudWatch group per
service.

**9. Tear it down when you are done looking at it.** For `dev`,
`postgres_deletion_protection = true` means the database survives a `terraform destroy` and
has to be released deliberately — which is the correct default and an annoyance exactly once.

---

## 3. What it would cost to actually stand this up

us-east-1, on-demand list prices, 730 hours per month, an environment with effectively no
traffic. These are **estimates from list prices and they will drift**; treat the shape as
reliable and the second decimal place as decoration. Data transfer and request-count charges
are guesses at the low end, because at real traffic they stop being the interesting number.

### `dev` — the environment that would really be stood up

| Service | What is configured | $/month |
| --- | --- | ---: |
| Application Load Balancer | 1 ALB, ~1 LCU | 19.50 |
| NAT Gateway | 1 (`single_nat_gateway = true`), ~20 GB egress | 33.75 |
| Public IPv4 | 1 address, on the NAT gateway | 3.65 |
| RDS PostgreSQL | `db.t4g.small`, single-AZ, 20 GiB gp3, 7-day backups | 25.65 |
| ElastiCache Redis | `cache.t4g.micro`, 1 node | 11.70 |
| ECS Fargate — `api` | 2 × 0.5 vCPU / 1 GB | 36.05 |
| ECS Fargate — `worker` | 1 × 0.5 vCPU / 1 GB | 18.00 |
| ECS Fargate — `beat` | 1 × 0.25 vCPU / 0.5 GB | 9.00 |
| Secrets Manager | 4 secrets + the RDS-managed one, at $0.40 each | 2.00 |
| CloudWatch Logs | ~2 GB ingested, 30-day retention | 1.05 |
| ECR | ~2 GB of deduplicated layers | 0.20 |
| S3 | ~10 GB of exports | 0.25 |
| Data transfer out | nominal | 1.00 |
| | **Total** | **≈ $161** |

### `local` — the floor, i.e. the compose stack in AWS

One of everything, no backups, nothing that costs money for durability.

| Service | What is configured | $/month |
| --- | --- | ---: |
| Application Load Balancer | 1 ALB, minimal LCU | 17.45 |
| NAT Gateway | 1, ~10 GB egress | 33.30 |
| Public IPv4 | 1 address | 3.65 |
| RDS PostgreSQL | `db.t4g.micro`, 20 GiB gp3, **no backups** | 14.00 |
| ElastiCache Redis | `cache.t4g.micro`, 1 node | 11.70 |
| ECS Fargate — all three | 3 × 0.25 vCPU / 0.5 GB | 27.05 |
| Secrets Manager | 5 secrets | 2.00 |
| Everything else | logs, ECR, S3, transfer | 1.30 |
| | **Total** | **≈ $110** |

### The number that actually decides this

**The ALB and the NAT gateway are ~$55/month before a single container runs.** That is half
the floor, and it is fixed: it does not shrink when the application is idle, and there is
nothing to tune. An AWS environment for this app therefore does not have a $20 version.

The rest is negotiable, and here is what each lever is worth on the `dev` estimate:

| Lever | Saving | What it costs you |
| --- | ---: | --- |
| Run `dev` 12h × 5d instead of 24×7 | ~$36 | Nothing, if it is genuinely a dev environment. This is the biggest single win and needs a scheduled scale-to-zero. |
| `cpu_architecture = "ARM64"` | ~$13 | Every push becomes `docker buildx --platform linux/arm64`, and an x86 image pushed to an ARM64 task definition fails at task start rather than at deploy. |
| `worker` on `FARGATE_SPOT` | ~$13 | An interrupted worker is `task_acks_late` doing its job. The cluster already has the capacity provider attached. |
| No NAT: tasks in public subnets | ~$22 net | Saves $36.50 of NAT, costs $14.60 of public IPv4 on four task ENIs, and puts the application tier on the internet. Not recommended; listed because it is the one people reach for. |
| `db.t4g.micro` instead of `small` | ~$12 | 1 GiB of RAM. It OOMs under a real seed, which is why `dev` does not use it. |

All five together take `dev` to roughly **$77/month**, and the first two are the only ones
without a real trade attached.

### What is *not* in these numbers

- **Multi-AZ RDS doubles the instance line** (+$23 on `dev`). Neither environment enables it.
- **A second NAT gateway** is +$33.75 and is what AZ-independent egress costs.
- **Container Insights** is off. It is billed per metric per hour and there are a lot of
  metrics for three services.
- **Performance Insights and enhanced RDS monitoring** are off, for the same reason.
- **A domain, a certificate and Route 53** — $0.50/month for a hosted zone plus whatever the
  domain costs. ACM certificates are free.
- **The frontend.** S3 + CloudFront for the SPA is a few dollars and is not in this
  configuration at all.
- **Traffic.** Every per-GB and per-request line here assumes an environment nobody is using.

---

## 4. Two things the application cannot do yet

Both were found while writing the task definitions, and both are honest blockers rather than
oversights in the Terraform. Neither is fixed here, because ANV-40 changes no application
code.

**The S3 tier will not work against real AWS.** `app/clients/s3.py` has two properties that
are individually correct and together make a real-S3 deployment impossible:

- `Settings.s3_endpoint_url` defaults to `http://minio:9000`, so it cannot be *unset* by
  omitting the variable — and `S3Client` passes whatever it holds straight to
  `aioboto3.client(endpoint_url=...)`, where `""` is not the same as `None`. The task
  definitions set `S3_ENDPOINT_URL=""` because that is what the operator *means*, behind a
  `TODO(ANV-s3-aws)` in `modules/compute/locals.tf` that a test asserts is still there.
- `S3Client._require_configuration` refuses to build a client when `S3_ACCESS_KEY_ID` or
  `S3_SECRET_ACCESS_KEY` is blank, and its module docstring explains exactly why: without
  explicit credentials botocore falls back to the ambient identity and would quietly write to
  a real bucket. That guard is good. Its consequence is that the app **must** be handed a
  static key pair, which is why `modules/storage/iam.tf` creates an IAM *user* rather than
  granting the Fargate task role.

The fix is one small application change — let an empty `s3_endpoint_url` mean `None` and let
blank credentials mean "use the chain" — after which the IAM user, its access key and the
`s3-credentials` secret all get deleted and the task role grants the bucket directly.
The Terraform is written so that is a deletion, not a rewrite.

**Nothing anywhere is using TLS to Postgres or Redis.** `rds.force_ssl` is not set and
`transit_encryption_enabled` is false, in both cases because `Settings` builds a plain
`postgresql+asyncpg://` and a plain `redis://` URL. Turning either on without the matching
application change refuses every connection. Both are inside a private subnet with no route
to the internet, so this is a defence-in-depth gap rather than an open door — but it is a gap,
and it is the first thing to close before anything real is behind it.

---

## 5. Where the safety comes from

- **No credential of any kind is in this repository**, and none is used by any test. The
  Terraform is verified with `terraform init -backend=false && terraform validate`, which
  needs no AWS account.
- **No `aws_secretsmanager_secret_version` and no `aws_iam_access_key` resource exists**
  anywhere in `backend/infra/`. Both would put a plaintext secret into Terraform state.
  `test_infra_terraform.py` fails if either ever appears.
- **No account id, no region literal, no ARN and no key-shaped string is in any `.tf`.** The
  account comes from `data.aws_caller_identity`, the partition from `data.aws_partition`, and
  the region from a variable with no default. Also asserted.
- **`.tfvars` files are committed here, against the usual advice**, because these ones hold
  no secrets — every secret lives in Secrets Manager. `.gitignore` carries an explicit
  exception and a test asserts both the exception and the absence of anything secret-shaped.

## 6. Nothing runs `terraform apply`

Not a script, not a workflow, not a hook. `scripts/` (ANV-37) has no Terraform command in it;
`.github/workflows/ci.yml` (ANV-38) is read-only — `contents: read`, no `id-token`, no secrets
— and does not mention Terraform at all. `test_infra_terraform.py` asserts all of that.

If a deploy is ever automated, it needs `permissions: id-token: write` on its own job in a
**separate workflow file**, so that a deploy credential is never in scope for a test run. That
is ANV-38's conclusion and it still stands.
