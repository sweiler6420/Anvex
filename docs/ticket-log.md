# Anvex ticket log

Per-ticket record of what shipped and why: the decisions taken, the bugs found, and the
notes each ticket handed to the next. Appended as each ticket completes.

This is the **archive**. For current status, environment facts and the carry-overs that are
still live, read [`build-log.md`](./build-log.md) — that is the session handoff document.
Each ticket's record also lives on its Linear issue.

---

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

### ANV-42 — Done *(inserted mid-epic; Stephen's call)*
Commit `7122ea6`. **Verified independently:** `454 passed / 192 skipped`, ruff clean, `passlib`
absent from both `uv.lock` and the synced venv, `bcrypt` resolved to **5.0.0**, and a real round
trip producing a `$2b$12$` hash that verifies (wrong password and garbage hash both `False`).

`app/utils/security.py` now imports `bcrypt` and nothing else non-stdlib. Same six public names,
same signatures — `AuthService` and the API tests needed no edit. `exceeds_bcrypt_limit` runs
**first** in `hash_password`, so an over-long password raises `PasswordTooLongError` before bcrypt
5.x can raise its own `ValueError`.

**Cost factor is `BCRYPT_COST_FACTOR = 12`, stated explicitly** rather than taking
`bcrypt.gensalt()`'s default — 12 is what passlib used, so latency and security margin are identical
across the swap, making this a pure dependency change rather than a silent security one. Naming it
also means an upstream default change cannot move our work factor in either direction.

`tests/unit/test_security.py` passed **unchanged** apart from the one permitted line
(`{"passlib"}` → `{"bcrypt"}`) plus an appended `TestLegacyPasslibHash`. All 12 broken-stored-hash
cases still return `False` against bcrypt 5 — including `$2b$99$…` and whitespace, which raise
`ValueError("Invalid salt")` that the existing `except` already covers.

**Process note — the orchestrator supplied a fabricated hash.** The ticket prompt included a
"legacy passlib hash" for `correct horse battery staple` that was invented, not generated. The agent
caught it: re-hashing against that digest's own salt did not reproduce it, and passlib 1.7.4 +
bcrypt 4.0.1 installed in a throwaway `uv run --no-project` environment rejected it too. It then
generated a genuine hash from that exact removed stack and used that instead, recording how in a
comment. **Lesson: never hand an agent a synthetic fixture presented as real output** — write the
instruction to generate it, or generate it first and paste verified output.

### ANV-12 — Done · **E3 Auth complete**
Commit `fb29f39`, 11 files, ~74 new tests. **Verified independently:** `715 passed / 5 skipped`
with `db-test` up, `520 passed / 200 skipped` with it stopped, ruff clean, `/me` correctly declared
before `/{user_id}`, and the cross-account refusal confirmed to happen *before* any query.

**Routes:** `POST /v1/users` (201, public), `GET /v1/users/me`, `GET /v1/users/{user_id}`.
`/me` must stay declared first or Starlette tries to parse `"me"` as a UUID.

**Decision: `GET /v1/users/{user_id}` is self-only, and another user's id answers 404 — not 403.**
Registration is self-service, so "any authenticated caller" is "anybody at all"; the old behaviour
made every account's email readable by a stranger who spent thirty seconds signing up. Anvex has no
directory, no social graph and no admin role, so nothing needs the capability. 403 was rejected
because it *confirms the account is real*, which is the half of the information worth protecting —
the refusal is raised without querying, so timing is silent too.

**`PasswordTooLongError` → `ValidationError`** is translated in `UserService._hash`, the only place
it can be. The test first proves the fixture is not theatre: `"漢" * 25` is 25 characters (inside
ANV-8's 72-*character* cap, so `UserCreate` accepts it) and 75 *bytes* (outside bcrypt's limit). The
API test asserts **422, not 500** — 500 is the failure mode without the translation.

`securitySchemes` now appears in the OpenAPI document and provably did not before — verified by
building an app with ANV-11's route set (`None`) and with ANV-12's (`OAuth2PasswordBearer`).
ANV-11's probe route was mounted by a test fixture, so it never reached the document.

**Carried into ANV-13 onward:**
1. **The refusal shape for owned resources** — "not yours" is a 404 identical to "does not exist",
   raised *before* any query. **ANV-15's watchlists need exactly this**; a `ForbiddenError` there
   would confirm which watchlist ids are real.
2. **Pre-check for the message, constraint for the correctness.** Keep the `*_exists` call so the
   409 can name `details.field`, *and* catch the `IntegrityError` — with
   `await session.rollback()` **first**, because Postgres aborts the transaction and refuses
   everything after it — then map the `uq_<table>_<column>` name to the same `ConflictError` and
   re-raise anything unrecognised. ANV-13's `StockRepo.delete` RESTRICT case is the same pattern.
3. **Assert constraint-name mapping in `tests/integration/`.** A hand-built `IntegrityError` only
   tests itself. `test_services_user.py` blinds the pre-checks with a `BlindUserRepo` — the real
   repo and real SQL, with only the two "is it taken" lookups forced to answer "free" — which is
   exactly the state a race loser is in.
4. **Translate `app/utils/` exceptions at the service.** Utils raise builtins by layering rule;
   uncaught they become 500s for input the API should have refused.
5. Shared fakes grew: `StubSession` now counts `commits`/`rollbacks`, so "the service owns the
   transaction" is a unit assertion. `FakeUserRepo` gained `email_exists`, `username_exists`,
   `create`, and a one-shot `create_error`.
6. API tests can override **two service factories onto one shared fake repo**, which is what made
   register → login → `/me` testable end to end with no database.

**Process note:** the agent amended its own commit after noticing it had written test counts before
counting them, and flagged it against the ANV-42 lesson. The corrected figures match an independent
count.

### ANV-13 — Done
Commit `1b34cfd`, 10 files, 88 new tests (46 unit, 42 API), 100% coverage on the service.
**Verified independently:** `803 passed / 5 skipped` with `db-test` up, `608 passed / 200 skipped`
with it stopped, ruff clean, both route-shadowing control tests passing.

**Routes:** `GET /v1/stocks` (paginated, search), `GET /v1/stocks/{stock_id}`,
`GET /v1/stocks/by-ticker/{ticker}`. All authenticated. Read-only — a unit test asserts the
service's public surface is exactly the three read methods, so the scope boundary is enforced
rather than remembered.

**It checked my premise about route ordering and found it wrong.** The ticket warned that
`/{stock_id}` might shadow `/by-ticker/{ticker}`. It cannot: `/{stock_id}` compiles to a
single-segment pattern and Starlette's default converter never matches `/`, so a two-segment
literal route is safe **whichever order it is declared**. Rather than write a comment asserting
that, it built a control app with the declarations reversed and proved it still resolves — and a
second control proving a *one*-segment literal (`/stocks/popular`) genuinely is shadowed when
declared second: literal-first 200, literal-second 422. That is the `/users/me` trap reproduced.
The convention is kept anyway; now the real rule is documented. It also found the actual shadow in
this router: `GET /v1/stocks/by-ticker` with no ticker matches `/{stock_id}` and answers 422.

**Ticker normalisation lives in the service, and it verified rather than assumed.** A probe app
confirmed ANV-8's annotated `Ticker` **does** reach a path parameter — then it deliberately did not
use it there, because that annotation only covers callers arriving over HTTP while ANV-22's ingest
holds a plain vendor symbol. Two API tests make that load-bearing: the OpenAPI document declares an
unconstrained `string` (so nothing at the edge folds case), and `/by-ticker/%20aapl%20` resolves
anyway. On the unit side `FakeStockRepo.get_by_ticker` is faithfully exact and case-sensitive, so a
service that forgot to normalise fails instead of silently passing, and the assertion is pinned at
the boundary (`repo.calls == [("get_by_ticker", "AAPL")]`), not just on the result.

**It resolved an apparent contradiction in my instructions.** The ticket said "clamp to
`MAX_PAGE_LIMIT`"; ANV-8's docstring said over-large limits are "rejected at the edge rather than
quietly clamped". Both are right, at different layers: the route's `Query(ge=1, le=MAX_PAGE_LIMIT)`
**rejects** with 422 so an HTTP client is never silently given a shorter page, while the service's
`resolve_page_limit` clamp protects callers with no request to reject (Celery, scripts). The clamp
is not cosmetic — `Page.limit` itself carries `le=MAX_PAGE_LIMIT`, so an unclamped 10,000 would fail
the envelope's own validation and become a 500. `resolve_page_limit` was added to
`app/schemas/pagination.py` beside the bounds it enforces, rather than re-derived per service.

**Note for later tickets:** this FastAPI version does **not** flatten `include_router` into
`app.routes` — included routers appear as opaque objects with no `.path`/`.routes`. Iterating
`app.routes` to find mounted paths no longer works; read `app.openapi()["paths"]` instead.

### ANV-14 — Done
Commit `0f8ce67`, 14 files, 190 new tests (79 domain, 47 service unit, 46 API, 18 integration).
**Verified independently:** `993 passed / 5 skipped` with `db-test` up, `780 passed / 218 skipped`
with it stopped, ruff clean, all six chart-contract tests passing. 100% coverage on
`app/domain/stock.py`, `app/domain/stock_data.py`, `app/services/stock_data.py`.

**Routes are nested, overriding the backlog's sketch.** `GET /v1/stocks/{stock_id}/data` and
`GET /v1/stocks/by-ticker/{ticker}/data` rather than `GET /v1/stock-data?ticker=…`. The argument:
a candle series is not a top-level collection anybody browses — it is *this stock's* prices,
`ON DELETE CASCADE`d with it and never queried across securities. Nesting means the parent cannot be
omitted, a missing parent is a 404 from the URL itself, and the two ways of naming a security stay
ANV-13's two rather than a third convention.

**That surfaced a real bug in the old endpoint.** Its `search: Optional[str] = ""` default meant a
bare `GET /v1/stock_data` returned *every* stock's candles interleaved in one date-ordered list —
not a plottable series, and silently wrong rather than an error.

**The domain rules** (`DateRange`, `PageWindow`, `CandleQuery`, `resolve_*`): bounds inclusive at
both ends; an inverted range (`start > end`) is a **422, not a silently empty 200**, because the
caller has a bug and an empty page hides it; `start == end` is a single trading day. A `datetime`
bound is narrowed to `.date()` — **`datetime` subclasses `date`**, so it passes every type check and
would otherwise compare against a `DATE` column, making `16:00 Mon → 09:30 Mon` read as an inversion
rather than one day. Deliberately **no span cap and no clock**: paging already bounds the response,
and "is this range in the future" needs an exchange-to-timezone map that does not exist.

**Live candle, showing both halves of the chart contract:**
```json
{"open_price":"1234.0678","close_price":"1234.5678","volume":2048,
 "stock_id":"8d8b495a-…","datetime":"2026-01-05T09:30:00"}
```
Naive `datetime` (no `Z`, no offset), prices as quoted strings with the fourth decimal intact. The
API tests assert on **raw response text** rather than parsed floats, and the test carrying the
`tzinfo is None` assertion also carries the "do not fix this" explanation.

**`normalise_ticker` moved down, not sideways.** ANV-13 put it in `app/services/stock.py`; a second
service needing it would have meant two services importing each other. It now lives in
`app/domain/stock.py` and is re-exported from its old home, with a test asserting the re-export is
the same object — so ANV-13's import path and tests are untouched.

**A deliberate omission worth knowing:** the ticker route does not call
`StockDataRepo.list_for_ticker`. The service must resolve the stock anyway to produce the 404, and
once it holds the row it holds `stock_id` — the leading column of the `(stock_id, date, time)`
index. Joining `stocks` a second time would be work for nothing. `FakeStockDataRepo` therefore
implements only `list_for_stock` and is deliberately unable to distinguish an unknown stock from an
empty one, which is what makes a service that skipped the parent lookup fail.

### ANV-15 — Done
Commit `fe354d5`, 12 files, **499 new tests** (351 domain, 67 service unit, 58 API, 23 integration).
**Verified independently:** `1492 passed / 5 skipped` with `db-test` up, `1256 passed / 241 skipped`
with it stopped, ruff clean, 26 ownership-isolation tests green. 100% coverage on
`app/domain/watchlist.py` and `app/services/watchlist.py`.

**It found a bug the orchestrator authored.** `(max_position or -1) + 1` — written into ANV-9's
carry-over notes, and from there into `CLAUDE.md` and `app/repos/watchlist.py`'s docstring — is
wrong, and not for the empty case. **`0` is falsy**, so appending to a watchlist holding one stock
at position 0 gives `(0 or -1) + 1 == 0`: a tie the ordering can never resolve. `next_position` tests
`is None`, a unit test and an integration test pin the `max_position == 0` case, and both pieces of
orchestrator text were corrected.

**`reposition(positions, *, stock_id, destination)`.** Input and output are the same
`{stock_id: position}` shape, so results feed back in and moves compose. Keyed on `stock_id` with a
destination and **nothing else** — there is deliberately no way to say "the thing at index 3",
because that sentence cannot be verified server-side. The arithmetic is a list splice over the
canonical order: remove, insert, renumber all. No direction branch, no `position == index`
assumption.

**Confirmed the ownership gap by reading the old code:** `get_watchlist` *does* check
`user_id == current_user.user_id`; `reposition_stock` does **not** (it queries `WatchlistData` by
`watchlist_id` alone and never references `current_user`); `add_watchlist_stock` does **not** either,
and additionally never checks the watchlist exists at all.

**Two further defects it found unprompted:** `add_watchlist_stock` unconditionally **prepends** at
position 0, shifting everything down — so watching a new stock re-sorted the user's whole
arrangement every time. And there was **no "list my watchlists" route**, so a lost `watchlist_id`
was unrecoverable.

**The ownership gate is a single private `_resolve_owned`** that every id-taking use case must pass
through, and it deliberately uses `get_by_id` (the row alone, no eager load) so a refusal does no
work on the child table — no size-proportional timing signal. The isolation sweep is parameterised
over all five use cases × four assertions, and **its case list is derived from `vars(WatchlistService)`
minus a named exempt set**, so adding a use case without isolation coverage fails the suite. The fake
repo is deliberately unhelpful — `get_by_id` filters on `watchlist_id` alone, exactly like the real
query — so a service that dropped the `user_id` comparison passes every other test in the file and
fails only these.

**Reorder edge cases:** up, down, no-op, to first, to last, single item, empty list, stock absent,
destination past the end / `== n` / negative (all 422, never clamped, never wrapping),
non-contiguous positions, non-zero-based, all-identical (legal — `position` has no unique
constraint), negative stored positions. Plus a property sweep over **every** `(size, origin,
destination)` for lists of 1–7, asserting the result is a permutation with positions exactly
`0..n-1`, the moved stock at the requested index, others in original relative order, and every move
reversible.

Routes: `POST/GET /v1/watchlists`, `GET/DELETE /v1/watchlists/{id}`,
`POST /v1/watchlists/{id}/stocks`, `DELETE|PATCH /v1/watchlists/{id}/stocks/{stock_id}`. The reorder
is a `PATCH` returning 200 (the old one was a `PUT` returning **201** for a move that created
nothing).

**Repo-wide formatting settled here.** The agent noted `ruff format --check` would reformat 37
files, i.e. formatting was never enforced. The orchestrator ran `ruff format` across the repo (122
files, tests still green) and added the check to ANV-38's CI scope, so it cannot drift again.

### ANV-16 — Done · **E4 Core features complete**
Commit `61538b8`, 22 files, 272 new tests. **Verified independently:** `1764 passed / 5 skipped`
with `db-test` up, `1499 passed / 270 skipped` with it stopped, `ruff check` and
`ruff format --check` both clean across 137 files. 100% coverage on `app/services/politician.py`.

**The `app/data/` loader pattern, set here** — this was the first user of that layer:

- **A data file is an envelope, not a bare array:** `{"provenance": "...", "rows": [...]}`, and
  `load_document` **refuses a file whose `provenance` is missing or blank.** That is the whole point
  of the key: fabricated reference data later being read as sourced data is the failure mode, and
  the only defence that cannot be forgotten is making an unattributed file unloadable. *(The
  orchestrator mutation-tested this: a file with no `provenance` is genuinely refused.)*
- Rows validate against the resource's `XCreate` at load, so a bad row fails naming the file, row
  index and field — not as an `IntegrityError` mid-insert.
- `SeedDataError` is a plain `ValueError`, **deliberately untranslated**: `app/data/` does not import
  `app/domain/errors.py`, because a broken checked-in file is a repo defect reached from a script,
  never a request, so there is no status code for it to become. An AST test over every module in
  `app/data/` forbids `sqlalchemy`, `httpx`, `app.repos`, `app.db` and any `app.` import beyond the
  resource's schema.

**Seed data is 54 rows and entirely synthetic**, generated from a seeded random name pool over a
hand-written per-state plan. The ids are Bioguide-*shaped* but are not Bioguide ids and resolve to
nobody. The file states all of this in its own `provenance` key and two tests assert it keeps saying
so. Coverage: 12 states + 1 stateless, 3 parties, 2 chambers, and all four nullable columns
exercised.

**`resolve_window` moved down.** Third caller, so it now lives in `app/domain/pagination.py`,
re-exported from `app/domain/stock_data.py`, with tests asserting the re-exports are the *same
objects* (a copy would pass every behavioural test while drifting). Leaving it put would have made a
politician service import a candle module. This generalises the "moves down" rule a second time,
*inside* `app/domain/`, for cross-aggregate rules.

**Dedupe and idempotency are two mechanisms, documented as not substituting for each other:**
within a run, `dedupe_politicians` collapses the batch with **last occurrence winning** (what a
sequential loop of upserts would have left); across runs, the repo's `ON CONFLICT DO UPDATE`. Proven
load-bearing by a pair of integration tests — the same duplicate batch raises `cannot affect row a
second time` through the repo, and succeeds through the service. Seed run twice against real
Postgres: 54 rows both times, 54 distinct ids.

**Two judgement calls worth keeping:** an unknown filter value is an **empty page, not a 422** —
`party` is free text in the database, and refusing "Whig" would mean Anvex claiming a vocabulary it
does not own. And `politician_id` is trimmed but **not** case-folded on lookup, unlike a ticker,
because a roster id is opaque and folding would make a genuinely distinct id unfindable.

### ANV-17 — Done
Commit `2cbe41b`, 5 files, 131 new tests, **100% coverage on `app/clients/base.py`** (222
statements). **Verified independently:** `1895 passed / 5 skipped` with `db-test` up,
`1630 passed / 270 skipped` with it stopped, ruff check and format clean across 140 files.

**A subclass is deliberately tiny:** two class attributes, one `auth_params()`, and one `async`
method per vendor operation that calls `self.get_json(...)` and validates into a model. **No `try`,
no status check, no retry loop, no logging in a subclass** — the base owns all of it.

**Four named timeouts rather than one**, because a bare `timeout=5` sets all four and hides which
was meant: connect 5s (a handshake is fast or the host is gone), read 15s (a vendor is allowed to
think), write 10s, pool 5s.

**Retry, and the 429 decision.** 4xx is never retried. 5xx and `httpx.TransportError` (timeouts are
a subclass, so one clause covers the network family) get 3 attempts, 0.2→0.4→cap 2.0s, jittered
**downward** so a fanned-out job does not resynchronise into a second herd. 429 is treated as its own
case — a "not now", not a "never": a shorter budget (one retry, enough to ride a burst boundary but
not to keep hammering a vendor that means it), and **`Retry-After` honoured but capped at 2s**. A
vendor asking for 60s is asking for longer than a request may be held open, so the call fails
immediately with `retry_after` in `details` and the caller reschedules. **No code path can wait an
unbounded time**, and the loop is bounded twice — by attempt count *and* a 20s wall-clock budget,
since attempts alone do not stop three slow-but-not-dead responses adding up.

Also non-retryable, asserted by call count: a malformed 200 body (a vendor answering HTML is broken,
not blipping), and a 3xx — `follow_redirects=False`, because following one would resend the
credential-bearing URL to a host the vendor chose.

**Credential redaction uses two independent tests**, because either alone has a hole: the parameter
*name* is credential-shaped, **or** its *value* is one of this call's secrets (a vendor that names
its key `u` defeats the first; a key we never enumerated defeats the second). `REDACTED` is spelled
as a word rather than `***` because `urlencode` turns `*` into `%2A` — the same guarantee in a form
nobody greps for. The diagnostic parts of the query survive on purpose: blanking the whole query
string would keep the secret safe and make the log useless.

*Orchestrator verification note:* my first check appeared to show value-based redaction failing. It
was my own error — `redact_url(secrets=...)` takes plain `str` and I passed a `SecretStr`, which
never compares equal. Re-run with the right type, both paths fire. The base itself unwraps
`get_secret_value()` before building the set, so the plumbing is correct.

**The AST sweep** parses every module in `app/clients/` and fails on `sqlalchemy`, `requests`,
`app.repos`, `app.db`, `app.models`, `app.services`, `app.schemas`, `app.jobs`, `app.api`, any
`app.` import outside a four-name allow-list, anything but `ExternalServiceError` from
`app.domain.errors`, a `time.sleep` call, or a bare `print`. It carries a non-vacuity test **and**
`test_the_sweep_would_catch_a_violation`, which runs the checkers over synthetic violating source —
a checker that cannot fail proves nothing.

**One documented deviation:** CLAUDE.md §3 previously named services as the *only* layer allowed to
unwrap a `SecretStr`. Holding the key in the client is the safer design, so the agent amended that
sentence to name `app/clients/` as the one exception (unwrapped in the request builder, plaintext
never stored) rather than silently violating it.
