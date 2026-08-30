# Backend runbook

How to start it, migrate it, seed it, watch it, and what to do when it will not come up.
The architecture is in [`../../docs/architecture.md`](../../docs/architecture.md); this is
the operational half.

**Run everything through `scripts/`.** Every command exists twice — `scripts/<name>.ps1`
and `scripts/<name>.sh` — and the two halves take the same command line, because the
PowerShell halves parse `--flag` by hand rather than declaring `[switch]` parameters. They
already encode every trap on this page, so a session that uses them cannot fall into one.
`backend/tests/unit/test_repo_scripts.py` parses both halves and fails on drift.

---

## Cold start

```sh
cp .env.example .env      # then fill in ALPHAVANTAGE_API_KEY / NEWSAPI_API_KEY if you need them
./scripts/up.sh           # db, redis, minio, minio-init, api
./scripts/migrate.sh
./scripts/seed.sh
```

```powershell
Copy-Item .env.example .env
.\scripts\up.ps1
.\scripts\migrate.ps1
.\scripts\seed.ps1
```

Then:

- API — <http://localhost:8000>, interactive docs at `/docs`, schema at `/openapi.json`
- Liveness — `GET /health` · Readiness — `GET /health/ready`
- MinIO console — <http://localhost:9001>

`up` takes a target: `core` (the default — `db`, `redis`, `minio`, `minio-init`, `api`),
`celery` (the `worker` and `beat`, behind the `celery` compose profile), `frontend` (the
Vite dev server on 5173, behind the `frontend` profile), `db-test` (the throwaway Postgres
the suite dials on 5433), or `all`. Anything after the target is handed to
`docker compose up`, so `./scripts/up.sh core --build` works.

The whole clean-slate sequence is `./scripts/reset-db.sh --yes` — delete the dev database
volume, recreate, migrate, seed. It takes about 25 seconds end to end and asks for
confirmation without `--yes`.

## The commands

| Command | What it does |
| --- | --- |
| `up [core\|celery\|frontend\|db-test\|all]` | start services |
| `down [--volumes]` | stop everything; `--volumes` also deletes the named volumes (asks first) |
| `logs [service …]` | follow container output |
| `migrate [revision]` | `alembic upgrade`, to `head` by default |
| `makemigration "<message>"` | `alembic revision --autogenerate` |
| `seed` | load the checked-in seed data — wraps `backend/scripts/seed_politicians.py` |
| `test [backend\|frontend\|all] [args…]` | pytest, vitest-in-the-container, or both |
| `lint [backend\|frontend\|all]` | `ruff check` + `ruff format --check`, and eslint |
| `fmt [--check]` | `ruff format` + `ruff check --fix`. **Backend only** — the frontend has no formatter |
| `reset-db [--yes]` | delete the dev database volume, recreate, migrate, seed |
| `smoke [--clean --yes] [--live-vendor] [--skip-frontend]` | boot the whole stack and prove it end to end — twenty steps, [`../../docs/smoke.md`](../../docs/smoke.md) |

`fmt` stopping at the backend is deliberate and the script header says so: eslint is a
linter, prettier is not installed, and adding it is a repository-wide diff that wants its own
ticket.

## Configuration

**One `.env` at the repository root, read by every stack.** Compose injects it into every
service; Vite's `envDir` reaches one level up to find the same file, and only `VITE_`-prefixed
keys reach the browser. There is no second environment file, and none inside `frontend/`. Every new
key must be added to `.env.example` in the same commit — `backend/tests/unit/test_settings.py`
asserts the two agree.

`backend/app/settings.py` is the **only** module allowed to read the environment. Nothing
else calls `os.getenv`.

Published host ports live in `.env` and are a developer convenience; containers reach each
other by service name on the service's own port.

| Key | Default | Note |
| --- | --- | --- |
| `POSTGRES_HOST_PORT` | `5442` | **not 5432** — see the trap below |
| `POSTGRES_TEST_HOST_PORT` | `5433` | the `db-test` container |
| `REDIS_HOST_PORT` | `6379` | |
| `MINIO_HOST_PORT` / `MINIO_CONSOLE_HOST_PORT` | `9000` / `9001` | |
| `API_HOST_PORT` | `8000` | |
| `WEB_HOST_PORT` | `5173` | |
| `ALPHAVANTAGE_API_KEY` / `NEWSAPI_API_KEY` | empty | a blank key is refused *before* the request, with `details = {"reason": "not_configured", "setting": "<ENV_VAR>"}` |

## Migrations

```sh
./scripts/makemigration.sh "add the thing"     # autogenerate a draft
./scripts/migrate.sh                            # upgrade to head
./scripts/migrate.sh 0002                       # or up to a specific revision
```

`migrate` only ever upgrades: naming an older revision downgrades nothing. Downgrading is
`uv run alembic downgrade`, deliberately not wrapped, because it is not a routine operation.

Four rules, all of them enforced somewhere:

- **Every model change ships with a migration in the same commit**, and
  `alembic check` must report *No new upgrade operations detected*. The suite asserts it too,
  so a hand-edited migration that has drifted from the models fails the tests.
- **Autogenerate output is a draft.** Review and reformat it; never change what it *does*
  without re-running the check.
- **`Base.metadata.create_all` is banned outside tests.** An app that creates its own tables
  diverges from the migration history and the divergence is discovered in production.
- **Do not remove the `search_path` pin in `backend/app/db/migrations/env.py`.** The login
  role is also called `anvex`, so Postgres' stock `"$user", public` search path makes `anvex`
  the default schema — and Alembic represents the default schema as `None`, which breaks
  reflection, every foreign-key comparison and the `alembic_version` exclusion. Without the
  pin, autogenerate can never be empty.

`migrate`, `makemigration`, `seed` and `reset-db` run **on the host**, where `.env`'s
`POSTGRES_HOST=db` — a compose service name — does not resolve. The scripts translate it to
`localhost` and the published port, both read from that same `.env`, and only when `.env`
still says `db` and the environment carries no override. Set `POSTGRES_HOST` yourself and
they leave it alone. That translation lives in `scripts/_common.sh` / `scripts/_common.ps1`
and nowhere else.

## Seeding

`./scripts/seed.sh` wraps `backend/scripts/seed_politicians.py`, which loads
`backend/app/data/politicians.json` through `backend/app/data/loader.py`. It is idempotent
twice over — `INSERT … ON CONFLICT DO UPDATE` makes a second *run* safe, and deduplication
in `backend/app/domain/politician.py` makes a single run safe, because Postgres rejects a
statement whose conflict target is hit twice.

The loader **refuses a data file whose `provenance` is missing or blank**. That is the
point: reference data is the one thing somebody will later mistake for *sourced* data, and
the only reliable defence is making an unattributed file impossible to load. The shipped
roster says it is synthetic, and a test asserts it still does.

Distinct exit codes: the file being unusable and the database refusing are different
failures.

## Running the workers

```sh
./scripts/up.sh celery          # worker + beat
./scripts/logs.sh worker beat
```

Both stay behind the `celery` compose profile, because the day-to-day dev stack is the API
and its stores and an idle worker is a container competing for the same Postgres
connections.

- **`beat` is never scaled.** Two schedulers publish every tick twice.
- The worker's healthcheck is `celery inspect ping` pinned to its own node, so it answers
  "this worker is *consuming*" — a process that is up but not consuming looks identical from
  outside.
- Beat writes its schedule to `/tmp/anvex-celerybeat-schedule`. The default would put a
  binary shelve file in the bind-mounted source tree; losing the container-local one only
  means the next tick fires immediately.
- The intraday ingest fans out hourly and paces its vendor calls with a `countdown` per
  message. A run occupies `MAX_CALLS_PER_RUN × CALL_SPACING_SECONDS` seconds of wall clock,
  and a test asserts that stays inside the beat interval — overlapping fan-outs would double
  the call rate the spacing was chosen for.
- A dispatched target with no `ALPHAVANTAGE_API_KEY` fails fast with `reason:
  "not_configured"` and is **not** retried. Only transport errors, 5xx and rate limits are.

## Observability

Logs are structured (structlog) and every line inside a request carries the request id.

- **Every request carries `X-Request-ID`** — the inbound one when it is a safe short token,
  otherwise a generated UUID4. It is bound into the log context, echoed on the response, and
  repeated as `error.request_id` in the body, so a user quoting an error maps to one log line.
  CORS `expose_headers` includes it, so the browser can read it.
- **No bare `print` anywhere**; the client layer's AST sweep fails on one.
- **A vendor URL is never logged raw.** `redact_url` blanks any query value whose *name*
  looks like a credential **and** any value that *is* one of the call's secrets — two
  independent tests, because a vendor that names its key `u` defeats the first and a key we
  have not enumerated defeats the second. Bodies and headers are never logged at all, and a
  presigned URL is returned and written nowhere.
- **A 500 never returns its traceback.** The body is the fixed `An unexpected error
  occurred.` with empty `details`; the traceback is logged.

Useful log events: `app.startup`, `app.shutdown`, `health.database_unavailable`,
`watchlists.cross_account_access_refused`, `auth.recovery_requested` (with
`delivered=False`), `s3.request.failed`.

## Health and readiness

`GET /health` is **liveness**: it touches nothing, and it is what the container healthcheck
polls. `GET /health/ready` is **readiness**: a real `SELECT 1`, 503 when it fails, and it is
what `depends_on` conditions and an ALB target group would poll.

**Never wire the container healthcheck to `/health/ready`** — readiness depends on Postgres,
so doing that turns a database blip into an API restart loop.

## When it will not come up

| Symptom | Cause | Fix |
| --- | --- | --- |
| Host client reaches a database with the wrong tables, no error | **A natively installed `postgresql-x64-18` owns host port 5432.** On Windows both it and Docker's proxy bind 5432 successfully | compose publishes `db` on **5442** and `db-test` on **5433**. Never point anything at 5432 |
| `uv run pytest` dies with `os error 4551` | an Application Control policy blocks the generated console-script shim | `uv run python -m pytest`, always. `scripts/test.sh` already does |
| uv resolves the wrong environment | a stale `VIRTUAL_ENV` pointing at the old repository's venv | `$env:VIRTUAL_ENV = $null`, or use `scripts/` — `_common` clears it |
| `uv: command not found` | uv is not on `PATH` on the dev machine | it is at `%USERPROFILE%\.local\bin\uv.exe`; or use `scripts/`, which resolves it |
| A frontend command hangs forever | `docker compose exec` without `-T` in a non-interactive shell | `-T` is required. `scripts/` already passes it |
| `npm run build` emits a 330 kB bundle with `jsxDEV` in it | something set `NODE_ENV`. Vite honours an inherited one over its own mode, silently, with exit code 0 | **nothing sets `NODE_ENV`**, anywhere. CI counts `jsxDEV` in `dist/` and fails on anything but zero |
| `.\up.ps1 2>&1` fails on a healthy stack | Windows PowerShell turns a native command's stderr into a *terminating* error when the caller redirects it, and `docker compose` writes progress to stderr | the scripts check native exit codes by hand inside an `$ErrorActionPreference = 'Continue'` window |
| `alembic check` reports operations after a clean autogenerate | the `search_path` pin was removed from `env.py` | put it back |
| `MissingGreenlet` from a relationship | it was not eager-loaded | name it in `.options(selectinload(...))` in the repo method; if the load is optional, add a *separate* method rather than a flag |
| Test suite green but a whole tier silently absent | tiers skip when their container is down — that is by design locally | `-rs` shows the skip reasons; CI asserts each tier is reachable before running |
| `docker pull` fails with `no such host` for `production.cloudfront.docker.com` | intermittent DNS to CDN-fronted hosts on this machine — `Resolve-DnsName` answers fine from the same shell | transient. Retry |
| Em dashes mangled after appending to a doc from PowerShell | Windows PowerShell 5.1 `Get-Content` reads a BOM-less UTF-8 file as ANSI | read with `[System.IO.File]::ReadAllText($path, (New-Object System.Text.UTF8Encoding($false)))` |
| `Event loop is closed` from asyncpg, on the *second* thing a host script does | `app/db/engine.py` keeps a module-level engine, so pooled connections opened under one `asyncio.run` outlive the loop they were bound to | dispose **inside** the loop: `try: … finally: await dispose_engine()`. `app/jobs/base.py` does this for a Celery task; `backend/scripts/smoke.py`'s `in_a_loop` does it for a script (ANV-41) |
| Registration is a 422 naming `email`, for an address that looks fine | `email-validator` refuses **special-use** names — `.invalid`, `.localhost`, `.test`, `.local`, `.arpa`, `.onion` | use `example.com`, which is IANA-reserved for documentation and is *not* on that list |
| A `docker compose exec … sh -c 'cat > /tmp/x'` writes an empty file | nothing was piped to it — `-T` makes stdin a pipe, it does not fill it | pass the payload as the subprocess's `input` |
| A "no HTTP calls" claim, and yet `client.request.completed` in the log | the smoke's stubbed ingest injects an `httpx.MockTransport` at `BaseHTTPClient`'s `transport=` seam, so the client logs a completed request that never left the process | expected. The line shows `apikey=REDACTED`, which is the redaction working |
| The suite's DB/S3/broker tiers start skipping right after a smoke run | `smoke --clean` is `docker compose down --volumes` for the **whole project**, and that takes `db-test` with it | `up db-test` before the next `test backend`, or the tiers skip politely and the run looks green with 276 fewer tests in it |
| `scripts/smoke.sh` passes locally and would not on a clean machine | the images are cached here. `--clean` destroys the **volumes**, not `anvex/api:dev` | only a runner that has never built them proves that half; locally, `--clean --yes` is as far as it goes |

**The two whole-stack failures ANV-41 actually hit are not in this table**, because neither
stops the stack coming up — they are contract failures a running system reports happily.
Both are in [`../../docs/architecture.md`](../../docs/architecture.md) §6: a spent refresh
token is never revoked, and a rotation inside the same second returns the identical token.

## Things that are absent on purpose

Before filing any of these, read
[`../../docs/architecture.md`](../../docs/architecture.md) §6 — they are decisions with
their reasoning written down, not gaps nobody noticed. In short: `POST /v1/auth/recovery`
sends no mail; there is no deep historical backfill; nothing mounts `download_url` or any
other storage route; `S3Client` cannot talk to real AWS S3; there is no TLS to Postgres or
Redis; and `/portfolio` is a page for a feature the API does not have.

## Deployment

There is not one. [`../infra/`](../infra/) holds a Terraform skeleton that has **never been
applied** — no AWS account has been touched and the running cost is $0.00. Verifying it
needs no account and no installed Terraform on `PATH`:

```sh
cd backend/infra
terraform init -backend=false && terraform validate && terraform fmt -check -recursive
```

Read [`../../docs/aws-deployment.md`](../../docs/aws-deployment.md) before deciding to stand
it up: **≈ $110/month at the floor, ≈ $161 for a usable `dev`**, of which the load balancer
and the NAT gateway are ~$55 before a single container runs.

`terraform init` leaves a ~685 MB `.terraform/` directory. It is gitignored, but this
repository lives inside OneDrive, which will happily sync every megabyte — remove it when
you are done validating.
