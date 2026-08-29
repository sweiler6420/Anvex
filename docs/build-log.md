# Anvex build log

Running record of the monorepo build. **Read this first after any session restart** — it is the
handoff document. Pair it with [`backlog.md`](./backlog.md) (the ticket specs) and
[`../CLAUDE.md`](../CLAUDE.md) (the architecture contract).

## How this build runs

- The backlog in `docs/backlog.md` is executed **sequentially**, one ticket at a time.
- Each ticket is delegated to a **single clean subagent**, never run in parallel.
- Every ticket ships tests for its own changes and appends framework-level conventions to
  `CLAUDE.md`.
- Tickets live in **Linear**, team **Anvex**, project
  [Anvex — Monorepo Build](https://linear.app/reality-drift/project/anvex-monorepo-build-7dc988651385).
  Linear's `ANV-N` numbering matches `backlog.md` exactly.

## Environment facts that bite

| Fact | Consequence |
| --- | --- |
| `uv` at `C:\Users\sweil\.local\bin\uv.exe`, **not on PATH** | prepend it in every Python command block |
| Stale `VIRTUAL_ENV` → `AverageInvestorApi\venv` | must be cleared (`$env:VIRTUAL_ENV = $null`) or uv targets the wrong env |
| **A native `postgresql-x64-18` service owns host port 5432** (auto-start, PID confirmed) | compose publishes `db` on **5442**. On Windows the native server *and* Docker's proxy both bind 5432 successfully, so a host client silently reaches the wrong database with no error |
| **`uv run pytest` is blocked by an Application Control policy** (`os error 4551`) | always use `uv run python -m pytest`; `uv run ruff` is fine |
| No `node` / `npm` installed | all frontend tooling runs **inside Docker** |
| `gh` CLI installed 2026-08-28, on the *user* PATH | only shells started afterwards resolve it; older ones need the full path |
| Docker daemon frequently stopped | check before depending on it |
| Bash tool sandbox has a minimal PATH | use the PowerShell tool for uv / docker / git |

## Legacy repos — read-only

`AverageInvestorApi`, `AverageInvestorService`, `AverageInvestorWeb` under
`C:\Users\sweil\OneDrive\Documents\Projects\AverageInvestor\` are reference only and must never be
modified. What each contributes to Anvex:

- **Api** — FastAPI, sync SQLAlchemy 1.4, `avg_inv` schema. Tables `users`, `stocks`, `stock_data`,
  `watchlists`, `watchlist_data`, `politicians`. Routers: auth (JWT access+refresh), users, stocks,
  stock_data, watchlist (with reorder), news (hardcoded blob). Config via
  `settings/config.{env}.json`.
- **Service** — AlphaVantage 5-min intraday → pandas → Postgres, with EC2/Lambda glue. Becomes
  Celery jobs + an AlphaVantage client.
- **Web** — CRA + React 18 + Tailwind + react-router v6. Auth context + `PersistLogin`/`RequireAuth`
  + axios refresh interceptors. Pages: Home (already Anvex-branded), Login, SignUp, Recovery,
  Unauthorized, Research/Portfolio (placeholders). Plus the ~1200-line `binpacking` window system
  and its widgets.

---

## Status

| Ticket | Title | Status |
| --- | --- | --- |
| ANV-1 | Monorepo scaffold and architecture contract | **Done** |
| ANV-2 | Backend uv project and settings | **Done** |
| ANV-3 | Async database layer and Alembic | **Done** |
| ANV-4 | App factory, middleware and error contract | **Done** |
| ANV-5 | Docker Compose stack and backend Dockerfile | **Done** |
| ANV-6 | Pytest harness | **Done** — *E1 Foundation complete* |
| ANV-7 | Models and initial migration | **Done** |
| ANV-8 | Pydantic schemas | Next |
| ANV-9 … ANV-41 | see `backlog.md` | Not started |

### ANV-1 — Done
Commits `0ccc0df`, `9ae4224` on `main`.

Created the repo at `C:\Users\sweil\OneDrive\Documents\Projects\Anvex`: `backend/` + `frontend/`
split, `docs/`, `scripts/`, root `.env` (from `.env.example`), `.gitignore`, `.gitattributes`,
`README.md`, and `CLAUDE.md` — the layering contract that every subsequent ticket follows.
Backend skeleton `app/{api,clients,data,db,deps,domain,jobs,middleware,models,repos,schemas,services,utils}`
plus `backend/{docs,infra,scripts,tests}`. Wrote the 41-ticket backlog.

Remote `origin` → `https://github.com/sweiler6420/Anvex.git` (created empty by Stephen; `gh` is
not installed).

### ANV-2 — Done
Commit `b0ceb36`, 22 files. **Verified independently by the orchestrator:** `uv run python -m
pytest` → 28 passed, `app/settings.py` at 100% coverage; `uv run ruff check .` → clean.

- `backend/pyproject.toml` — uv-managed `anvex-backend`, `requires-python >=3.12`, hatchling with
  `packages = ["app"]`. 13 runtime deps + a 6-package `dev` group, unpinned. Ruff (line-length 100,
  py312, `E,F,I,UP,B,C4,SIM,RUF`, `known-first-party = ["app"]`) and pytest
  (`asyncio_mode = "auto"`, coverage on `app`) both configured here. `uv.lock` committed
  (86 packages).
- `backend/app/settings.py` — one `Settings(BaseSettings)` reading `REPO_ROOT / ".env"`, resolved
  from `__file__` so cwd does not matter. `extra="ignore"` lets the frontend's `VITE_*` keys sit in
  the shared file harmlessly. Secrets (`postgres_password`, `jwt_secret_key`,
  `s3_secret_access_key`, both API keys) are `SecretStr`. Properties `postgres_dsn`,
  `postgres_sync_dsn`, `cors_origins`, `redis_url`; `get_settings()` is `lru_cache`d.
- Every `app/` and `tests/` subpackage now has an `__init__.py`.

**Design note worth keeping:** the DSN helpers are plain `@property`, *not* `computed_field`,
because a computed field would serialise the embedded Postgres password back into `repr()` and
`model_dump()` and undo the `SecretStr` fields.

**Carried into ANV-3:**
- `postgres_sync_dsn` names the `postgresql+psycopg` driver but **`psycopg` is not installed**.
  Either add `psycopg[binary]` if Alembic needs a blocking connection, or keep `env.py` fully async
  (which `CLAUDE.md` §4 prefers) and treat the sync DSN as offline/tooling-only.
- `postgres_schema` (`"anvex"`) is the value `Base` must bind its `MetaData(schema=...)` to.
- ANV-4's `app/deps/` should wrap `get_settings()` in a `Depends` provider rather than
  constructing `Settings()` directly.

### ANV-3 — Done
Commit `6350809`, 12 files. **Verified independently:** 41 passed / 1 skipped (the migration test
skips cleanly with no database), ruff clean, and the throwaway Postgres container was removed.

- `app/db/engine.py` — lazily-created async engine; `pool_pre_ping`, pool sizing as module
  constants (deliberately *not* new env vars), and `dispose_engine()` for the ANV-4 lifespan.
- `app/db/session.py` — `get_sessionmaker()` (`expire_on_commit=False`) and an
  `@asynccontextmanager get_session()` that rolls back on exception and always closes. It never
  commits — the service owns the transaction.
- `app/db/base.py` — `Base` on `MetaData(schema="anvex", naming_convention=...)` with
  `pk_`/`fk_`/`uq_`/`ix_`/`ck_` templates.
- Alembic wired fully async. `alembic.ini` carries **no** `sqlalchemy.url`; `env.py` reads it from
  `get_settings()` and escapes `%` for configparser. `include_schemas=True` plus an `include_name`
  hook restricting reflection to `anvex` — without the hook, autogenerate sees `public` and proposes
  dropping whatever it finds there.

**Two subtle things it found, both now commented in the code:**

1. `env.py` runs `CREATE SCHEMA IF NOT EXISTS anvex` before `context.configure`, then calls
   `connection.commit()` explicitly. That commit is load-bearing: the bootstrap `execute` autobegins
   a transaction, which makes Alembic's own `begin_transaction()` a no-op nested block — so
   `upgrade head` **reported success while committing nothing**. This was hit live, not theorised.
2. The first revision's `downgrade` drops `pgcrypto` but deliberately leaves the `anvex` schema.
   `alembic_version` lives in it, and Alembic deletes the revision row *after* `downgrade()` returns
   — dropping the schema takes the version table with it and the downgrade errors.

**Carried into ANV-4:**
- Lifespan shutdown: `await dispose_engine()`. Startup needs nothing — the engine is lazy and opens
  no socket at import.
- `app/deps/get_session` should wrap `app.db.session.get_session()`:
  `async with get_session() as session: yield session`. Do not build a second sessionmaker.
- `/health/ready` should issue a real `SELECT 1`; `pool_pre_ping` handles dead connections, so a
  failure there is genuine.

**Carried into ANV-7:**
- Subclass `app.db.base.Base` only. `Base.metadata` already carries `schema="anvex"` — do **not**
  set `__table_args__ = {"schema": "anvex"}` on models.
- Do not name constraints by hand; the naming convention generates them and keeps diffs stable.
- `gen_random_uuid()` is available, and `0001_bootstrap` is your `down_revision`.
- `alembic revision --autogenerate` runs a ruff `check --fix` post-write hook, so generated
  migrations land lint-clean.
- `psycopg` was **not** needed and is not installed; `postgres_sync_dsn` remains unused.

### ANV-4 — Done
Commit `3370387`, 25 files. **Verified independently:** 138 passed / 1 skipped, 98% coverage,
ruff clean, and a live `uvicorn` run confirmed `/health` 200, `/docs` 200, `/health/ready` 503.

- `app/domain/errors.py` — stdlib-only exception hierarchy. **The status mapping deliberately does
  not live on the exception classes**; it lives in `app/middleware/errors.py`, which is what keeps
  `app/domain/` free of HTTP. Lookup walks the MRO, so a future subclass inherits its parent's
  status and an unmapped one degrades to 500 rather than crashing the handler.
- `app/middleware/` — `request_id` (pure ASGI), `logging` (structlog + access log), `errors`
  (mapping + all four handlers), and `setup.install_middleware()` as the single place stack order
  is decided.
- `app/main.py` — `create_app(settings)` factory plus a module-level `app`. `/health` (liveness,
  no I/O) and `/health/ready` (readiness, real `SELECT 1` via `app/db/health.ping`).
- `app/deps/` — `get_session` wrapping ANV-3's context manager, and `get_settings_dep`.
- `app/api/v1/__init__.py` — an empty `APIRouter(prefix="/v1")` aggregator, so ANV-11 onward adds
  routers with a one-line change.

**The error envelope is now a hard contract** (documented in `CLAUDE.md` §4). Every non-2xx —
domain error, pydantic 422, unknown route, unhandled crash — returns the same four keys, with
`details` as `{}` rather than `null` so clients index it unconditionally and branch on `code`.

**Bug found live:** a 500 bypassed the request-ID middleware entirely. Starlette's
`ServerErrorMiddleware` sits *outside* all user middleware, so it sends its response without
passing through the send wrapper — meaning the one response a client most needs to correlate was
the only one missing the header. Fixed by having `error_response()` set `X-Request-ID` itself,
with the middleware de-duplicating.

**Carried into ANV-5:**
- The container healthcheck must hit **`/health`**, not `/health/ready` — readiness depends on
  Postgres, so using it as a healthcheck restart-loops the API whenever the DB blips. Use
  `/health/ready` for the `depends_on` gate and, later, the ALB target group.
- Entrypoint `uvicorn app.main:app` (or `app.main:create_app --factory`). Startup opens no socket,
  so the API boots even if Postgres is still starting.
- Logs go to stdout as JSON whenever `ANVEX_ENV != local`, console-rendered when it is.
- The compose `web` origin must appear in `API_CORS_ORIGINS`.

**Carried into ANV-6:**
- `tests/conftest.py` holds only `ALLOWED_ORIGIN` and the `settings` / `app` / `client` fixtures —
  **extend these, do not add parallel ones.**
- `client` uses `ASGITransport(app=app, raise_app_exceptions=False)`. That flag is load-bearing:
  without it the transport re-raises through `ServerErrorMiddleware` and you can never assert on
  the 500 body a real client actually receives.
- The `app` fixture builds a fresh instance per test so `dependency_overrides` cannot leak.
- `ERROR_BODY_KEYS` in `tests/api/test_middleware.py` is the canonical assertion set — worth
  promoting to a shared helper when the harness lands.

### ANV-5 — Done
Commit `2aa2ba4`, 6 files. **Verified independently:** 138 passed / 6 skipped, ruff clean, and no
leftover containers, volumes or networks after teardown. The agent's own run showed the API
`healthy`, `/health` 200, `/health/ready` `{"status":"ok","database":"ok"}` 200, and
`alembic upgrade head` succeeding **inside** the container.

- `backend/Dockerfile` — two stages on `python:3.12-slim-bookworm`, uv copied from
  `ghcr.io/astral-sh/uv`. Deps install from `pyproject.toml` + `uv.lock` before any source is
  copied, with a BuildKit cache mount. Non-root `anvex` (uid 1000). `HEALTHCHECK` on `/health`
  using stdlib `urllib` — no curl, so no apt layer at all. **432 MB final.**
- Two decisions that matter: the venv lives at **`/opt/venv`, not `/app/.venv`**, because the dev
  service bind-mounts `./backend` over `/app` and would otherwise both hide it and shadow it with
  the host's Windows-built `.venv`; and `--no-install-project` means source is imported from the
  working directory, which is what makes bind-mount + `--reload` work with no reinstall.
- `docker-compose.yml` — `db` (5442), `db-test` (5433, **tmpfs, no volume**), `redis`, `minio` +
  `minio-init`, `api` (8000). `worker`/`beat` sit behind a `celery` profile and `web` behind a
  `frontend` profile, so a plain `docker compose up` never starts them and no Celery app was
  invented ahead of ANV-21. `worker`/`beat` also set `healthcheck: disable: true` — they inherit
  the API image, whose healthcheck polls `:8000/health`, and a worker serving no HTTP would sit
  permanently unhealthy.

**The real problem it found:** the first compose test failed with
`InvalidPasswordError: password authentication failed for user "anvex"` against `localhost:5432`
while the API *inside* the network was perfectly healthy. `netstat` showed two listeners on
`0.0.0.0:5432` — the host's native `postgresql-x64-18` service and Docker's proxy. On Windows both
bind successfully and the host client silently reaches the wrong server. Hence `db` on 5442.

**Carried into ANV-6:**
- `db-test` is `localhost:5433` from the host, `db-test:5432` inside the network. Credentials are
  the shared `POSTGRES_USER`/`POSTGRES_PASSWORD`; the database is `POSTGRES_TEST_DB=anvex_test`.
- `Settings` has **no test-DSN field yet** — `postgres_host`/`port`/`db` still point at the app
  database, so the harness must build the test DSN itself (new settings fields or an override
  fixture). Deliberately left as ANV-6's call.
- `db-test` is tmpfs-backed with no named volume, so every start runs `initdb`: it has no `anvex`
  schema and no `alembic_version`. The harness must run migrations itself.
- **Do not default the test database to port 5432.** See above.
- The opt-in pattern is established: `ANVEX_COMPOSE_TEST=1` gates Docker-dependent tests, and
  `CLAUDE.md` §6 now requires the default suite to run with the daemon stopped.

### ANV-6 — Done · **E1 Foundation complete**
Commit `b4fd642`, 16 files. **Verified independently, both directions:**
`177 passed / 5 skipped` with `db-test` up, `164 passed / 18 skipped` with it stopped, ruff clean.
I also inspected the test database afterwards: the only surviving table was `alembic_version`
holding `0001_bootstrap` — real migrations ran and **zero** test rows survived, so rollback
isolation is genuine rather than asserted.

**Test DSN — `Settings` was deliberately left alone.** `CLAUDE.md` §4 says of `db-test` that
"nothing in `app/` ever knows it exists", so adding `postgres_test_*` fields would have shipped a
test-only concern into the production config surface. Instead `tests/database.py` owns a
`HarnessDatabaseSettings` reading the *same* root `.env` (§2 holds), borrowing
`postgres_user`/`postgres_password` from the real `Settings`. `POSTGRES_TEST_PORT` uses
`AliasChoices(..., "POSTGRES_TEST_HOST_PORT")` so the compose publication port is the single number
to change — no drift trap.

**Rollback mechanism.** `db_connection` opens a transaction that is never committed; `db_session`
binds to it with `join_transaction_mode="create_savepoint"`, so a service's `session.commit()`
releases and reopens a savepoint — defaults fire, constraints trip, rows stay visible — while the
outer transaction is untouched and rolled back at teardown. The agent **mutation-tested** this:
swapping the teardown `rollback()` for `commit()` fails 3 tests, including one asserting `5 == 0`.

Two supporting decisions: `NullPool` on the session engine (an asyncpg connection belongs to the
loop that opened it, so pooling across per-test loops is the classic hang), and `alembic upgrade
head` rather than `create_all`, so tests exercise the schema production actually gets. The harness
builds its Alembic `Config` **without** `alembic.ini`, because loading it calls `fileConfig()` which
rips out pytest's `caplog` handler mid-session.

**Fixtures every later ticket uses:** `settings`, `app`, `client`, `mock_http` (respx),
`database_available`, `db_engine`, `db_connection`, **`db_session`** (the usual one), `db_app`,
`db_client`, `throwaway_database_url`, `_deterministic_fakes` (autouse).
`tests/helpers.py` adds `ERROR_BODY_KEYS`, `assert_error_envelope()`, `StubSession`,
`override_session()`.

**Skipping is fixture-driven, not directory-driven** — `pytest_collection_modifyitems` applies the
`db` marker from `item.fixturenames`, so `-m "not db"` deselects the tier while a respx-only test
living in `tests/integration/` still runs with Docker stopped.

**Carried into ANV-7:**
- Factory *infrastructure* exists (`tests/factories/base.py`: `Factory[ModelT]`, `@register` /
  `factory_for`, `reset_randomness()` / `sequence()`, `fake`). ANV-7 adds one module per model
  group, each a `@register`ed `Factory[Model]` implementing `defaults()`, re-exported from
  `tests/factories/__init__.py`.
- **Unique columns must use `self.sequence()`, never faker.** Seeding resets per test, so
  `fake.email()` returns the same address twice within one test and trips the constraint.
  `test_faker_alone_would_repeat_within_a_test` documents the trap.
- A factory **flushes, never commits.**
- Delete the temporary `scratch_table` fixture from `conftest.py` and rewrite
  `tests/integration/test_harness.py` against a real model + factory.

Also: `tests/integration/test_migrations.py` was rewritten onto `throwaway_database_url`. It had
been using the *app* settings (`POSTGRES_HOST=db`), so it could never run from the host — it has
now executed for the first time.

### ANV-7 — Done
Commit `1370fa1`, 16 files, migration `0002_core_tables`. **Verified independently:**
`225 passed / 5 skipped` with `db-test` up, `170 passed / 60 skipped` with it stopped, ruff clean.
I also ran `alembic upgrade head` and `alembic check` myself against a fresh database —
**"No new upgrade operations detected."** Models and migration genuinely agree.

**The bug that nearly made empty autogenerate impossible.** The Postgres login role is *also* named
`anvex`. Postgres' stock `search_path` is `"$user", public`, so `current_schema()` returned `anvex`
and SQLAlchemy reported it as the connection's **default** schema. Alembic represents the default
schema as `None`, and ANV-3's `include_name` compared `name == SCHEMA` — so it filtered out our own
schema, reflected nothing, and proposed re-creating all six tables. Fixing that exposed two more:
reflected FKs carried `referred_schema=None` against the metadata's `"anvex"` (every FK reported
dropped-and-added), and `alembic_version` looked like a removed table because its exclusion keys on
`version_table_schema`. Fix: `server_settings={"search_path": "public"}` on **alembic's engine
only**, making `anvex` non-default so all reflection is fully qualified, plus an `include_name` that
resolves `None` back to the real default before comparing. Invisible before ANV-7 because there were
no tables to reflect.

**Schema deviations from the old repo, all deliberate:**

| Change | Why |
| --- | --- |
| `watchlist_data` real composite PK | the old `__mapper_args__` key was ORM-only; the table had none, so a stock could be added to a watchlist repeatedly |
| `ticker_symbol` 5 → **16** | `BRK.B`, `BTC-USD` do not fit in 5 |
| `company` unique → **indexed, not unique** | GOOG and GOOGL are both "Alphabet Inc." |
| `isin` NOT NULL → **nullable, unique** | model and migration disagreed; AlphaVantage returns no ISIN, so requiring it would block ANV-22 from ever creating a stock |
| prices NUMERIC(8,2) → **(12,4)** | old ceiling was 999,999.99 — BRK.A is within an order of magnitude, and overflow is a hard `DataError` mid-ingest |
| `stock_data.id` → **BIGSERIAL** | the old default referenced a sequence no migration ever created |
| `created_at` → **TIMESTAMPTZ** | the migration created a naive `TIMESTAMP` |
| `UNIQUE (stock_id, date, time)` | ANV-22's upsert conflict target — idempotency as a database rule, not a code habit |
| range index on **`date` alone** | the unique constraint's btree already serves "this stock, this date range"; a duplicate would be waste. What it *cannot* serve is a cross-stock date window, because `date` is not its leading column |
| `username` → VARCHAR(50), `email` → VARCHAR(320) | model said unbounded, migration said `VARCHAR(20)` — below ANV-12's own 7+ minimum. 320 is RFC 5321's max forward path |

**`ondelete` (the old schema declared none, so deleting a user simply failed):** `stock_data` →
CASCADE, `watchlists.user_id` → CASCADE, `watchlist_data.watchlist_id` → CASCADE, but
`watchlist_data.stock_id` → **RESTRICT** — a stock is reference data, and deleting one people are
actively watching is a mistake worth surfacing. Mirrored on the ORM with `passive_deletes=True`, and
**`passive_deletes="all"` on `Stock.watchlist_entries`** — without it the ORM loads the membership
rows and tries to NULL their `stock_id`, turning a deliberate RESTRICT into a confusing NOT NULL
violation. `position` is deliberately **not** unique per watchlist: ANV-15's reorder swaps two
ordinals and a non-deferrable constraint would reject the intermediate state.

**Carried into ANV-8 (schemas) and ANV-9 (repos):**
- Column naming is `<entity>_id`, not `id` — the sole exception is `stock_data.id`.
- `users.password` keeps its legacy name and holds the **hash**. `UserOut` must never expose it.
- **Only these are nullable:** `stocks.isin`; `politicians.state`, `chamber`, `dob`, `gender`.
  Every other `XOut` field is non-optional.
- Prices are `Decimal` (NUMERIC(12,4)), **not float**. `date` and `time` are separate columns;
  ANV-14 recombines them into the `datetime` field the charts expect — that is a schema/service
  concern, not a column.
- Length caps to mirror in validators: username 50, email 320, ticker 16, company/market 150,
  ISIN 12, watchlist title 50, politician names 80.
- `WatchlistData` has **no surrogate key** — identity is the `(watchlist_id, stock_id)` pair.
- **Repos must eager-load**; lazy loading is impossible under asyncio.
  `test_models.py::TestRelationships` shows the exact `selectinload` chains. `Watchlist → entries`
  is already ordered by `position` on the relationship, so no caller sorts.
- `Stock` deletion is RESTRICT-guarded, so `StockRepo.delete` raises `IntegrityError` for a watched
  stock — ANV-13 should map that to `ConflictError`, not let it surface as a 500.
