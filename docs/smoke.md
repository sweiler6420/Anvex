# End-to-end smoke

The checklist behind `scripts/smoke.ps1` / `scripts/smoke.sh` (ANV-41). Twenty steps, in
order, from `docker compose up` to loading `/research` in a DOM with nothing but a refresh
token.

**This is the only thing in the repository that proves the pieces fit together.** 3,995
backend tests, 922 frontend tests, a `validate`-clean Terraform tree and a green CI each
prove one component with its neighbours replaced by fixtures. None of them would notice if
the API container could not reach Postgres by service name, if the published port were
wrong, if `alembic` and the app disagreed about the schema, or if the built bundle asked a
host that is not there.

## Run it

```powershell
.\scripts\smoke.ps1                     # against whatever is already running
.\scripts\smoke.ps1 --clean --yes       # destroy the volumes first: a boot from nothing
.\scripts\smoke.ps1 --skip-frontend     # API and worker only
.\scripts\smoke.ps1 --steps             # list the steps, run nothing
```

```sh
./scripts/smoke.sh
./scripts/smoke.sh --clean --yes
./scripts/smoke.sh --skip-frontend
./scripts/smoke.sh --steps
```

From nothing at all, the whole sequence is three commands:

```sh
git clone https://github.com/sweiler6420/Anvex.git && cd Anvex
cp .env.example .env
./scripts/smoke.sh
```

No API key is needed and none is asked for. `.env.example` carries working local defaults
for Postgres, Redis and MinIO; the two third-party keys are blank, and the smoke run
accounts for that rather than failing on it — see **The vendor leg** below.

Exit codes: `0` every step passed, `1` a step failed and the output names it, `2` the run
could not start (no Docker daemon, no `.env`, a refused `--clean` confirmation).

## What a failure looks like

Every step reports itself, what it expected and what it actually saw. A generic non-zero
exit is useless at two in the morning, so there is deliberately no such thing here:

```
FAILED at step 6/20: api-health — Liveness and readiness
  expected: GET http://localhost:8000/health and /health/ready to answer 200
  observed: /health 200, /health/ready 503 service_unavailable: The database is unavailable.
  hint:     a 503 from /health/ready is the API up but unable to reach Postgres by
            service name; a connection error is the published port (API_HOST_PORT)
```

[`backend/docs/runbook.md`](../backend/docs/runbook.md) holds the table of things that stop
the stack coming up, and every hint above points into it.

## The steps

| # | Step | What it proves | If it fails |
| --- | --- | --- | --- |
| 1 | `preflight` | The Docker daemon answers, `.env` exists, and whether an AlphaVantage key is configured — reported as a boolean, never printed | Docker Desktop is stopped, or `.env` was never copied |
| 2 | `clean` | With `--clean`, the stack and its **named volumes** are gone, so the boot below really is from nothing | A container that will not stop, usually because its image was removed underneath it |
| 3 | `compose-up` | `db`, `redis`, `minio`, `minio-init` and `api` start and the first three report **healthy** | A port conflict — a natively installed Postgres owns 5432, which is why compose publishes 5442 |
| 4 | `migrate` | `alembic upgrade head` from the **host**, through the published port | The host cannot resolve `POSTGRES_HOST=db`; the wrapper is what exports `localhost` and the port |
| 5 | `seed` | The checked-in politician roster loads, idempotently | Exit 1 is an unusable `app/data/politicians.json`; exit 2 is the database refusing |
| 6 | `api-health` | `/health` (liveness, no I/O) and `/health/ready` (the API reaching Postgres from **inside** the network) | A 503 is the compose wiring; a connection error is the published port |
| 7 | `cors` | A preflight from `http://localhost:5173` is allowed | `API_CORS_ORIGINS` — a mistake here fails only in a browser, and nowhere in any test |
| 8 | `register` | `POST /v1/users` creates an account; a second attempt is 409 `conflict` | A 422 naming `failed_rules` means ANV-43's password policy changed |
| 9 | `login` | Form-encoded login returns a `TokenPair`; a wrong password is 401 `unauthorized` | A 422 is usually a caller sending JSON — this route takes an OAuth2 **form** body |
| 10 | `refresh` | The exchange answers a working `TokenPair`, an **access** token presented where a refresh belongs is 401 `wrong_token_type`, and `/v1/users/me` accepts the rotated bearer | Note the two things deliberately **not** asserted — see below |
| 11 | `reference-data` | The rows step 5 wrote read back over HTTP — with a bearer, because **every** `/v1` route is behind the guard, reference data included | A 401 is the token; a 500 is the seed and the API on different databases |
| 12 | `security` | A security exists to ingest and to read, and is searchable through `GET /v1/stocks` behind the bearer | There is no `POST /v1/stocks`; the row is written through the repository, which is what a future admin route would use |
| 13 | `ingest` | One symbol, one month, fetched → session-filtered → watermark-filtered → written, in one transaction | See **The vendor leg** |
| 14 | `worker` | `jobs.health.ping` travels broker → worker → result backend and comes back naming the pid that ran it | The heartbeat touches no database and no vendor, so a failure here is the plumbing and only the plumbing |
| 15 | `ingest-task` | The **real** `jobs.ingest.ingest_symbol` task is published, consumed and reaches a terminal state | See **The vendor leg** — what "terminal" means depends on whether a key exists |
| 16 | `stock-data` | The candles read back over HTTP: prices are **quoted JSON strings**, `datetime` is **naive** | A JSON number silently loses the fourth decimal place; an offset on `datetime` means the exchange clock grew a zone |
| 17 | `watchlist` | A watchlist is created, the security is added, and the entries come back in `position` order | An empty list means the add landed on a different watchlist or a different account |
| 18 | `frontend-up` | The Vite dev server container starts and answers on its published port | `scripts/logs web`; a Vite that will not start is nearly always a config syntax error |
| 19 | `frontend-build` | `npm run build` produces a **production** bundle — zero `jsxDEV` calls — into a container-local `/tmp`, never back through the bind mount | Something set `NODE_ENV=development`; Vite honours an inherited one over its own mode, silently. An `EACCES` means someone pointed the build's output directory back at `/app` |
| 20 | `cold-load` | `/research` loads in jsdom from a **refresh token alone**, and the securities panel lists the ingested symbol | This is the guard, the interceptor, the rotation, the bearer replay and the API in one page load |

`--skip-frontend` drops the last three. Nothing else is optional: a smoke test with a menu
is a smoke test nobody can quote a passing run of.

## The vendor leg — read this before believing the output

Step 13 and step 15 are the only ones that can reach a third party. AlphaVantage's free
tier allows roughly **25 calls a day**, shared by everyone holding the key, so the default
behaviour spends none of them:

| Situation | Step 13 `ingest` | Step 15 `ingest-task` |
| --- | --- | --- |
| **Default** (no `--live-vendor`), **no key in `.env`** | **Stubbed** at the `app/clients/base.py` transport seam. The stub asserts the request — path, `function`, `symbol`, `interval`, `month` and the API key — so the client is proved to build exactly the call the live one would send | **Published for real.** The client refuses before opening a socket (`not_configured`), so the message proves broker → worker → task → client seam at **zero** cost. `FAILURE` with `ExternalServiceError` is the expected, asserted outcome |
| **Default**, **a key present** | Stubbed, as above | **Skipped**, and says so. Publishing it would quietly spend somebody's quota |
| `--live-vendor` (requires a key) | **One real call.** One symbol, one explicit month, retries disabled so a vendor 5xx cannot become three requests | **One real call**, executed by the worker, expected to succeed and to report what it wrote |

The run's header says which mode it is in, and the passing summary repeats it. **A stubbed
leg reported as a live one would be worse than no smoke test at all**, so it is stated
twice rather than implied once.

The stub authenticates with a key invented in `backend/scripts/smoke.py` and never with a
real one, which means the credential path is exercised in a run where no credential exists.
Nothing here prints, logs, stores or commits a key.

## What the smoke does **not** prove

Stated plainly, because a green run is easy to over-read:

- **No AWS.** Nothing in this path touches `backend/infra/`, and the Terraform has still
  never been applied. `POSTGRES_HOST` remains the only seam for pointing host-side tooling
  elsewhere; if this ever grows an "against a deployed environment" mode it must take a
  base URL as an argument rather than reading anything out of the infrastructure tree.
- **No S3.** MinIO is started because `api` depends on it, but no route mounts anything in
  `StorageService`, so nothing here uploads or downloads an object.
- **No mail.** `POST /v1/auth/recovery` logs `delivered=False` and returns 202. There is no
  mail client to smoke.
- **No `/portfolio`.** It is a documented non-feature and fetches nothing; asserting a page
  that makes no request would assert nothing.
- **Nothing about refresh-token revocation or uniqueness.** Step 10 tried to assert both
  and neither is true, which is the most useful thing this ticket found. A spent refresh
  token still works — `POST /v1/auth/refresh` mints a new pair and revokes nothing —
  and the "new" token is not necessarily a different string, because the payload is
  `{sub, type, iat, exp}` at one-second resolution with **no `jti`**, so a rotation inside
  the same second is byte-identical. One root cause; `docs/architecture.md` claimed the
  first, the code never did it, and no test noticed because every test mocked one side or
  the other. The document is corrected, §6 carries the row, and
  `TODO(ANV-refresh-revocation)` marks the place.
- **No browser.** The cold load runs in jsdom, which has no layout: the research desktop
  measures 0×0 and renders **empty**, which is why step 20 asserts the securities panel and
  the route testid and never a window. A real browser is a person's job.
- **It writes to the development database on purpose.** There are no fixtures and nothing
  is rolled back — a smoke test that isolates itself from the environment is testing the
  isolation. Step 13's stubbed candles are ordinary rows under a real ticker, and a later
  real ingest of the same month upserts straight over them.

## Afterwards

The stack is left running, including the Celery `worker` step 14 started. `beat` is
deliberately **not** started: its hourly entry publishes a real `ingest_all` fan-out, which
on a machine with a key would spend quota an hour after a smoke run nobody was watching.

```powershell
.\scripts\down.ps1                  # stop everything, keep the data
.\scripts\down.ps1 --volumes        # and delete it
```

```sh
./scripts/down.sh
./scripts/down.sh --volumes
```
