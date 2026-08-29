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
| ANV-8 | Pydantic schemas | **Done** |
| ANV-9 | Repositories | **Done** — *E2 Data layer complete* |
| ANV-10 | Security utilities and pure token domain | **Done** |
| ANV-11 | Auth service, dependencies and routes | **Done** |
| ANV-42 | Drop passlib, hash with bcrypt directly | Next *(inserted — Stephen's call)* |
| ANV-12 … ANV-41 | see `backlog.md` | Not started |

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

### ANV-8 — Done
Commit `0bba0b0`, 12 files, 123 new tests. **Verified independently:** `293 passed / 60 skipped`
with the DB stopped, `348 passed / 5 skipped` with it up, ruff clean.

**I mutation-tested the password guard myself.** Dropping a `SneakyOut` schema with a `password`
field into `app/schemas/` — not exported, not imported anywhere — made **both** guard tests fail;
removing it made them pass. The guard walks the package with `pkgutil.iter_modules` rather than
reading `__all__`, so it catches precisely the unexported schema nobody would check by hand, and
compares findings against a `ClassVar` allowlist so a future leak has to be written in deliberately.

**`Page[T]` = `{items, total, limit, offset, has_more}`.** Offset paging rather than cursor because
the frontend renders numbered pages and every Anvex list is a bounded set. `has_more` is a
`computed_field` so clients cannot each re-derive it and get the off-by-one wrong. Limit bounds
(`DEFAULT_PAGE_LIMIT=50`, `MAX_PAGE_LIMIT=200`) live in this module so the query parser and the
response cannot disagree.

**Deliberately no `XUpdate` for:** `StockData` (a candle is an immutable observation; ANV-22 upserts
whole rows) and `Politician` (reference data, idempotent seed on the natural key).

**Carried into ANV-11 (auth) — the important one:**
- `TokenPayload` is `{sub: UUID, exp, iat, type}` — standard `sub`, not the legacy `user_id` claim.
- **Verification must check `type`, not just the signature.** The old `/v1/refresh` called the same
  verifier on any token, so an *access* token could be traded for a fresh long-lived pair.
- `RefreshRequest` is a JSON body; the old endpoint took the token as a query parameter, i.e. into
  every proxy log.
- There is no `LoginRequest` — CLAUDE.md §4 fixes `OAuth2PasswordBearer`, so login takes
  `OAuth2PasswordRequestForm`.

**Carried into ANV-12 (users):** username ≥ 7 and password ≥ 7 (taken from the old sign-up form so
existing accounts stay valid), password capped at 72 bytes because bcrypt ignores the rest.
**Password *strength* rules are deliberately not in the schema** — those are `app/domain/`.
`PasswordChange` is separate from `UserUpdate` because it re-authenticates.

**Carried into ANV-9 / ANV-13:** `StockUpdate.isin` is genuinely nullable, so services must apply
updates with `model_dump(exclude_unset=True)` — the attribute alone cannot distinguish "clear it"
from "leave it". `WatchlistDetailOut` maps onto the `entries → stock` `selectinload` chain and
cannot be served from a lazily-loaded object under asyncio. `WatchlistCreate` accepts **no
`user_id`** — ownership comes from the token.

**Carried into ANV-14 (charts):** `StockDataPoint.from_row(row)` does the date+time recombination.
Its `datetime` is **naive on purpose** — `stock_data.time` is the exchange's local clock, and
stamping `+00:00` on 09:30 ET would move every candle. It is the only datetime in the API without an
offset.

**Wire-format change the frontend must handle:** `Decimal` serialises to a **quoted JSON string**
(`"1234.5678"`), which is what preserves the fourth decimal place. The old API emitted floats, so
chart code must `Number()` the value.

New dependency: `pydantic[email]` (without it `EmailStr` is a plain `str`).

### ANV-9 — Done · **E2 Data layer complete**
Commit `2170608`, 13 files, 132 new tests. **Verified independently:** `480 passed / 5 skipped`
with `db-test` up, `293 passed / 192 skipped` with it stopped, ruff clean. I also grepped the whole
`app/` package for `select(` outside `app/repos/` — **none**. The §3 layering rule is holding in
fact, not just on paper.

**Session is passed in, not held.** Every method takes `AsyncSession` first. That makes each repo
stateless, so they are exported as module-level singletons (`user_repo`, `stock_repo`, …) and a
service never has to be a factory. A repo that captured a session could also outlive it — a live
footgun once Celery tasks and request handlers share an engine.

**The login lookup is one statement**, `select(User).where(or_(email == ident, username == ident))`
— not two sequential lookups, because the second only runs on a miss and leaks which arm failed
through timing.

**Bulk upsert** uses `pg_insert(...).on_conflict_do_update(index_elements=["stock_id","date","time"])`
— naming the columns so Postgres infers ANV-7's unique index rather than hard-coding a constraint
name. Only prices and volume are in `set_`, so an updated candle keeps its `id`. Four tests hold the
property, including the same batch twice → `(3, 3)` with the table at 3 rows, and a revised candle
overwriting values while keeping its row. An empty batch returns 0 without issuing SQL (empty
`VALUES` is a syntax error).

**Search escaping:** `ilike` over ticker *or* company with `%`, `_`, `\` escaped, and blank meaning
"no filter". The old API's `contains(func.lower(search))` matched nothing and treated `""` as
everything.

**Carried into ANV-11 → ANV-16 — queries deliberately NOT provided:**
- **No "watchlist owned by user" query.** `get_by_id`/`get_with_entries` do not check ownership —
  that is authorization, a service concern. **ANV-15 must compare `watchlist.user_id` to the token
  subject itself** or it will serve one user another user's list. (The old API did this inline in
  the handler.)
- `get_by_ticker` is **exact and case-sensitive** — upper-casing user input is the service's
  one-line job; folding case in the repo would defeat the unique index. `list_stocks(search=...)`
  *is* case-insensitive: a search box is a different question from an identifier.
- No name search on politicians (state/party/chamber only); no `stock_data` bulk delete; **no
  repo-level dedupe on either `bulk_upsert`** — deduplicate in `app/domain/` first. A batch with an
  internal duplicate raises `cannot affect row a second time`, and a test documents that as a
  caller obligation.

**Things that will bite otherwise:**
- `limit` is a **required** keyword on every paginated method — the repo does not import
  `app.schemas`, so it cannot default to `DEFAULT_PAGE_LIMIT`.
- Paginated methods return `(rows, total)`; the service builds `Page[T]`. `total` is counted before
  the window, so `offset=99` over 4 rows gives `([], 4)`.
- `bulk_upsert` is a Core statement and does **not** update the session's identity map — re-read or
  `expunge_all` if ORM objects are still held.
- `update(session, instance, values)` sets exactly the keys given, which is what makes
  `model_dump(exclude_unset=True)` work: `{"isin": None}` clears, an absent key leaves alone.
- `max_position` returns `None` on an empty watchlist, not `-1`. The append rule
  `(max_position or -1) + 1` is a rule and belongs in `app/domain/watchlist.py`.
- Eager loading is a **separate method**, never a boolean flag. A test asserts the plain
  `list_for_stock` raises `MissingGreenlet` on `.stock` while the eager variant works, so the split
  cannot be mistaken for an oversight.

### ANV-10 — Done
Commit `4a5b2a8`, 8 files, 91 new tests, 100% coverage on both new modules. **Verified
independently:** `384 passed / 192 skipped`, ruff clean, and I exercised the real thing —
`hash_password` → `verify_password` round-trips, a wrong password and a garbage stored hash both
return `False`.

**The type check is enforced by signature shape, not by documentation.** There is no "just verify
this token" entry point. `decode_token`'s `expected_type` is keyword-only **with no default**, so
omitting it is a `TypeError` at the call site; the only other decoders name their type
(`decode_access_token` / `decode_refresh_token`) and take no `expected_type` at all. `create_token`
mirrors it, so a token cannot be minted without the claim that makes checking possible. Three tests
hold this, including a sweep over `__all__` asserting every `decode*` export either requires
`expected_type` or pins one. Expiry is checked *before* type, so an expired refresh token presented
as an access token reads as expired.

**bcrypt's 72-byte boundary: reject on hash, refuse on verify.** ANV-8's schema caps 72
*characters* but bcrypt counts 72 *bytes*, so a 25-character multibyte password passes validation
and still overflows — there is a test for exactly that (`"漢" * 25` = 75 bytes). Truncating would put
a long passphrase and its own 72-byte prefix in the same equivalence class, invisibly. The asymmetry
is deliberate: hashing is a write we control and should fail loudly before persisting; verifying
takes attacker-controlled input and must never turn a login into a 500. `verify_password` catches
`(ValueError, TypeError)` only — not bare `Exception` — so a `MissingBackendError` deployment fault
stays loud instead of degrading every login to "wrong password".

**Purity is enforced by AST inspection**, not prose: `TestPurity` parses `app/domain/auth.py` and
asserts no `fastapi`/`starlette`/`sqlalchemy` import, no `app.settings`, `app.*` imports exactly
`{app.domain.errors, app.schemas.auth}`, and no call to `now`/`utcnow`/`today`/`time`. Behaviourally
tokens are minted and read at 1999 and 2099 clock values, so any wall-clock read would fail.
`decode_token` passes `options={"verify_exp": False}` and compares itself — otherwise python-jose
reads the wall clock on the module's behalf.

**Dependency change:** `bcrypt` is pinned `>=4.0,<4.1`. **passlib 1.7.4 is its final release (2020)
and cannot work with newer bcrypt** — it probes its backend by hashing a >72-byte secret, which
bcrypt 5.0 turned into a `ValueError`, so `CryptContext(schemes=["bcrypt"])` raised before hashing
anything; 4.1 separately dropped `bcrypt.__about__`, which passlib reads for the version. The pin is
compatibility only — `security.py` enforces the byte boundary itself. **See the open question
below.**

**Carried into ANV-11:**
1. **Read the clock exactly once per operation** in the service (`datetime.now(UTC)`) and pass that
   one value down. Nothing below the service may call it. Unwrap `settings.jwt_secret_key` with
   `.get_secret_value()` — the domain takes a plain `str`.
2. **Login:** single-statement identifier lookup, then `verify_password`. On either miss raise a
   bare `UnauthorizedError` — never one that distinguishes the arms.
3. **Refresh:** `decode_refresh_token`, **re-read the user** (a deleted account must not keep
   refreshing), then mint a new pair. An access token on this path now raises `WrongTokenTypeError`
   — that is the fix, and it needs an API-tier test asserting the 401.
4. `get_current_user` must use `decode_access_token`, never `decode_token` directly.
5. Signup/password-change must catch `PasswordTooLongError` and translate it to
   `domain.errors.ValidationError` — `app/utils/` cannot import `app/domain/`, so the service is the
   translation point.
6. New public error codes for the frontend: `invalid_token`, `token_expired`, `wrong_token_type`.
   The client should refresh on `token_expired` and log out on the other two. No new
   `ERROR_STATUS_CODES` entry is needed — all three inherit 401 through `UnauthorizedError`'s MRO.

### ANV-11 — Done
Commit `817f514`, 68 new tests (39 unit, 29 API), 100% coverage on everything new.
**Verified independently:** `639 passed / 5 skipped` with `db-test` up, `452 passed / 192 skipped`
with it stopped, ruff clean, and the refresh regression test passes.

**Routes:** `POST /v1/auth/login` (form), `POST /v1/auth/refresh` (JSON body — query-string refresh
is now a 422), `POST /v1/auth/recovery` → **202**. Handlers are one line of body each.

**The regression this epic existed for**, verified live against real Postgres:
```
POST /v1/auth/refresh with an ACCESS token   → 401 wrong_token_type   (old API: 200 + fresh pair)
POST /v1/auth/refresh with the REFRESH token → 200, both halves rotated
```

**Hardening beyond the ticket:** on the *unknown identifier* login path the service still verifies
against a fixed decoy bcrypt digest, so a miss pays the same ~100 ms and response time is not an
account-existence oracle. The two failure arms were confirmed byte-identical apart from
`request_id`.

**Recovery is honest.** There is no mail client in `app/clients/` and none was invented. It logs
`auth.recovery_requested` with **`delivered=False`** and returns 202; the gap is an explicit
`TODO(ANV-mail)`, and **a unit test asserts that TODO is still present** so it fails the moment real
sending is wired in. Both branches return byte-identical responses — the old endpoint answered 404
`"User not found with username: <x>"`, a plain enumeration oracle.

**Corrected a bug in my own ticket text:** I specified `tokenUrl="v1/login"`, but the route is
`/v1/auth/login`. Nothing validates `tokenUrl` — it only points Swagger's *Authorize* button — so
that would have shipped a silently broken `/docs`. The agent used the real path and added
`test_the_swagger_authorize_button_points_at_the_real_login_route` asserting
`f"/{TOKEN_URL}" == LOGIN_URL` so it cannot drift.

**The pattern every later resource copies** (now in `CLAUDE.md` §3):
1. `XService(session, settings, *, xs: XRepo = x_repo)` — the keyword repo default is the seam that
   lets the unit tier use fakes and stay green with Docker stopped.
2. One `get_x_service` per resource in `app/deps/x.py`, plus an `XServiceDep` alias. **One seam
   only**; API tests override exactly that.
3. Clock read **once** at the service (asserted per method by a `CountingClock` — `clock.reads == 1`);
   `SecretStr` unwrapped only there.
4. Handlers: one service call, one schema, no `try`/`if`. Router owns its `prefix`/`tags`.
5. Shared fakes live in `tests/helpers.py` (`FakeUserRepo`, `make_user`, …) — add beside them, do
   not start a parallel set.

An AST test proves `HTTPException` never appears in the service module and that every `raise` names
an `AnvexError` subclass.

**Stale image, resolved.** The compose `api` image predated ANV-8 and lacked `pydantic[email]`, so
the container booted to `ImportError: email-validator is not installed`. The agent could not rebuild
— `docker compose build api` failed on `lookup auth.docker.io: no such host`. That turned out to be
**intermittent DNS inside the Docker daemon, not a network outage**: the host resolved
`auth.docker.io` fine (HTTP 200) at the same moment the daemon could not. A retry of
`docker compose build api` succeeded, and the stack now comes up healthy with `/health` and
`/health/ready` both 200 in-container and all five routes in the OpenAPI document.

**Rule of thumb for later tickets:** if a Docker pull or build fails with `no such host`, retry
before concluding the environment is offline. All base images (`python:3.12-slim-bookworm`,
`ghcr.io/astral-sh/uv`, `postgres:16-alpine`, `redis:7-alpine`, **`node:22-alpine`**) are already
cached locally, so builds mostly need PyPI/npm rather than Docker Hub.
