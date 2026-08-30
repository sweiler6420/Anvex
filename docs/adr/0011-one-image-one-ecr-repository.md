# ADR-0011 — One image and one ECR repository for api, worker and beat

## Status

**Accepted** — ANV-40. Recorded in ANV-39.

## Context

The backend runs as three processes: `api` (uvicorn), `worker` (celery worker) and `beat`
(celery beat). In `docker-compose.yml` all three are the **same image** — `anvex/api:dev`,
built once from `backend/Dockerfile` — and differ only in `command`. That is not an
accident of local convenience: the worker imports the same `app.services.*` the API does, so
a worker built from a different tree is a worker running different business rules against
the same database.

The instinct when writing the AWS side is to give each service its own ECR repository,
because each is its own ECS service. Three repositories means three tags, three pushes, and
three chances for the worker to be running a different commit from the API that enqueued its
work.

## Decision

**One ECR repository.** One image, three task definitions differing only in `command`,
exactly as compose does it. `backend/tests/unit/test_infra_terraform.py` asserts there is
exactly one `aws_ecr_repository` in the configuration.

The generalisation is the more useful half of the decision: **the AWS shapes are read off
the local topology, not invented.** Concretely, and each of these is a drift test that fails
if either side moves:

- the worker and beat command lines are compose's, character for character — including
  `--pool prefork` and `--schedule /tmp/anvex-celerybeat-schedule`;
- `--concurrency` equals `backend/infra/envs/local.tfvars`'s `worker_concurrency`;
- the RDS and ElastiCache engine versions track the compose image tags
  (`postgres:16-alpine` → family `postgres16`; `redis:7-alpine` → `default.redis7`);
- the ALB forwards to the port the Dockerfile `EXPOSE`s;
- the target group polls `/health/ready` while the container polls `/health`, per
  `backend/app/api/health.py`;
- the S3 lifecycle rule filters on `app.domain.storage.EXPORTS_PREFIX` and expires at
  `EXPORT_RETENTION`;
- the union of `container_environment` and `container_secrets` in
  `backend/infra/modules/compute/locals.tf` is asserted **equal, in both directions**, to
  `Settings.model_fields` upper-cased.

## Consequences

**The worker cannot run a different commit from the API.** One tag is the whole deployment,
and a rollback is one tag.

**The image carries entry points it does not use**, so it is marginally larger than three
specialised images, and a change to either half rebuilds and redeploys all three services.
For a codebase where the worker and the API share the entire service layer, that is the
honest shape: they are not independently deployable in any meaningful sense.

**`beat` is pinned to one task, as a literal, and stops its old task before starting the
new one** (`desired_count = 1`, `deployment_minimum_healthy_percent = 0`). Compose's own
comment is the argument: two schedulers publish every tick twice. A rolling deployment is
the second way to get two schedulers, and it is the one nobody thinks of.

**A new `Settings` field is a two-file change**, and the suite tells you after the fact: the
environment contract above is asserted equal in both directions, so a field added with no
home in `locals.tf` is a deployment silently running on that field's default — which for
`postgres_host` is `db`, a compose service name that resolves to nothing in AWS.

**Every one of those drift tests compares two artefacts, so a value that lives in only one
place is checked by nothing but reading it** — see
[ADR-0009](./0009-no-secrets-in-terraform.md)'s two review defects for what that costs.
