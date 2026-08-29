# Backend testing

The contract is [`CLAUDE.md` §6](../../CLAUDE.md); this is how it works in practice. The
harness lives in `backend/tests/` and is the same one every ticket writes against.

Run everything from `backend/`. **`uv run pytest` is blocked by an Application Control
policy on this machine — always use `uv run python -m pytest`.**

---

## The three tiers

| Tier | Directory | What belongs there | May touch |
| --- | --- | --- | --- |
| **unit** | `tests/unit/` | `app/domain/` and `app/utils/` — the rules, exhaustively. **Most tests live here.** | nothing: no fixtures, no I/O, no clock |
| **api** | `tests/api/` | route contracts: status codes, response shape, auth enforcement, validation errors | `client`, `app.dependency_overrides` |
| **integration** | `tests/integration/` | `app/repos/` and `app/services/` against real Postgres; `app/clients/` against mocked HTTP | `db_session`, `mock_http` |

Two rules that decide where a test goes:

- **Cover an edge case in `unit/`, not through the API.** If you are reaching for the API
  tier to test a rule, the rule is probably in the wrong layer — see `CLAUDE.md` §3.
- **A test never calls a live vendor API.** Clients are tested with `respx` via `mock_http`.

---

## Running

```powershell
# everything
uv run python -m pytest

# the fast tier only - no database, no HTTP, sub-second
uv run python -m pytest tests/unit

# everything except the database-backed tests
uv run python -m pytest -m "not db"

# one file, verbose, no coverage report
uv run python -m pytest tests/api/test_health.py -v --no-cov

# with the test database up
docker compose up -d db-test          # from the repo root
uv run python -m pytest
```

Coverage (`--cov=app --cov-report=term-missing`) is on by default via `pyproject.toml`.
Add `--no-cov` while iterating.

---

## The test database

`docker compose up -d db-test` starts a second Postgres, published on **`localhost:5433`**
(`db-test:5432` in-network), database `anvex_test`, sharing `POSTGRES_USER` /
`POSTGRES_PASSWORD` with the app database.

**Never point anything at 5432.** A natively installed `postgresql-x64-18` service owns that
port; on Windows both it and Docker's proxy bind successfully, so a host client silently
reaches the wrong server with no error at all.

The harness reaches it through `tests/database.py`, which reads three keys from the
repo-root `.env`:

| Key | Default | Meaning |
| --- | --- | --- |
| `POSTGRES_TEST_HOST` | `localhost` | set to `db-test` to run the suite inside the compose network |
| `POSTGRES_TEST_PORT` | falls back to `POSTGRES_TEST_HOST_PORT` (5433) | set to `5432` in-network |
| `POSTGRES_TEST_DB` | `anvex_test` | the same key compose feeds the container |

These deliberately are **not** fields on `app/settings.py`: `CLAUDE.md` §4 says nothing in
`app/` knows `db-test` exists, so the harness owns a small test-only `BaseSettings` instead.

`db-test` is tmpfs-backed with no volume, so it starts completely empty every time — no
`anvex` schema, no `alembic_version`. The `db_engine` fixture therefore runs **the real
Alembic migrations** (`upgrade head`, idempotent) once per session. Not `create_all`: the
tests exercise the schema production actually gets, which is the whole reason `CLAUDE.md`
§4 bans `create_all` outside tests.

### Skipping

**The default suite is green with Docker stopped.** A test that requests any `db_*` fixture
is auto-marked `db` and skips with a reason naming the fix; the database is probed once per
session. Nothing fails because a container is not running.

The marker is applied from the *fixtures a test requests*, not from its directory — so a
`respx`-only client test in `tests/integration/` keeps running with the daemon stopped.

`tests/integration/test_compose_health.py` needs the *whole* stack and is opt-in behind
`ANVEX_COMPOSE_TEST=1`.

---

## Fixtures

All of them live in `tests/conftest.py`. **Extend that file; never start a parallel one.**

### Application

| Fixture | Scope | What you get |
| --- | --- | --- |
| `settings` | function | `Settings` with CORS origins and log level pinned, so tests do not depend on the developer's `.env`. Override it in a module to pin one more field. |
| `app` | function | `create_app(settings)` — a **fresh app per test**, so `dependency_overrides` cannot leak. |
| `client` | function | `httpx.AsyncClient` over `ASGITransport`. `raise_app_exceptions=False` is load-bearing: without it a 500 re-raises into the test instead of returning the body a real client sees. |

### Database

| Fixture | Scope | What you get |
| --- | --- | --- |
| `database_available` | session | skips the test unless `db-test` answers |
| `db_engine` | session | `AsyncEngine` on the migrated `anvex_test`, `NullPool` |
| `db_connection` | function | one connection with an open, never-committed transaction |
| `db_session` | function | **the one you want** — an `AsyncSession` whose writes are rolled back |
| `db_app` | function | `app` with `deps.get_session` resolved to `db_session` |
| `db_client` | function | `client` bound to that app |
| `throwaway_database_url` | function | a brand-new empty database, dropped afterwards (for migration tests) |

### Other

| Fixture | Scope | What you get |
| --- | --- | --- |
| `mock_http` | function | a `respx` router intercepting every outbound `httpx` call |
| `_deterministic_fakes` | autouse | re-seeds faker and resets factory sequences before each test |

---

## Isolation, and why `commit()` still works

Each test runs inside a transaction that is rolled back at teardown, so tests never see
each other's writes and no cleanup code is needed anywhere.

`db_connection` opens a connection and begins a transaction that is **never committed**.
`db_session` binds to that connection with `join_transaction_mode="create_savepoint"`, so
the session opens a `SAVEPOINT` instead of joining the outer transaction. A service under
test can call `session.commit()` — as every service in this codebase does — and it behaves
normally: the savepoint is released, defaults fire, constraints trip, the row is visible to
every later query. The outer transaction is untouched, and rolling it back at teardown
takes the "committed" rows with it.

`tests/integration/test_harness.py` proves this: one test writes and commits, a later one
asserts on a fresh connection that nothing survived.

---

## Factories

`tests/factories/` holds faker-backed builders — one `Factory` subclass per model, next to
the model group it builds. Read `tests/factories/base.py`; the pattern is in its docstring.

```python
user = await UserFactory().create(db_session)  # added + flushed, not committed
draft = UserFactory().build(email="pinned@example.com")  # in memory only
```

Two rules:

- **Unique columns come from `self.sequence()`, never from faker.** Seeding is reset before
  every test, so `fake.email()` returns the same address twice within one test and a unique
  constraint fires.
- **A factory flushes, it never commits.** The transaction boundary belongs to the service
  layer (`CLAUDE.md` §3), and the rollback fixture owns it in tests.

Seeding is deterministic (`DEFAULT_SEED`), so a failure reproduces from
`pytest path::test_name` alone rather than only under the full suite.

---

## Shared assertions

`tests/helpers.py`:

- `ERROR_BODY_KEYS` and `assert_error_envelope(response, status=…, code=…)` — the error
  contract from `CLAUDE.md` §4, spelled out in exactly one place. It returns the inner
  `error` object so you can go on to assert on `details`.
- `StubSession` / `override_session(app, …)` — keep an API-tier test off Postgres when the
  route it is contract-testing happens to take a session.

---

## Writing a new test

1. Decide the tier from the table at the top. When in doubt, push it down.
2. Mirror the app's path: `app/services/watchlist.py` → `tests/integration/test_watchlist_service.py`.
3. Ask for the fixtures you need and nothing more — asking for `db_session` is what makes a
   test skip without a database, so do not request it "just in case".
4. Run `uv run python -m pytest` **and** `uv run ruff check .` before you call it done.
