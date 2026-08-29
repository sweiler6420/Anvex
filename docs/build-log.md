# Anvex build log

Running record of the monorepo build. **Read this first after any session restart** — it is the
handoff document. Pair it with [`backlog.md`](./backlog.md) (the ticket specs) and
[`../CLAUDE.md`](../CLAUDE.md) (the architecture contract).

## How this build runs

- The backlog in `docs/backlog.md` is executed **sequentially**, one ticket at a time.
- Each ticket is delegated to a **single clean subagent**, never run in parallel.
- Every ticket ships tests for its own changes and appends framework-level conventions to
  `CLAUDE.md`.
- Tickets are mirrored in **Linear**, team **Anvex**.

## Environment facts that bite

| Fact | Consequence |
| --- | --- |
| `uv` at `C:\Users\sweil\.local\bin\uv.exe`, **not on PATH** | prepend it in every Python command block |
| Stale `VIRTUAL_ENV` → `AverageInvestorApi\venv` | must be cleared (`$env:VIRTUAL_ENV = $null`) or uv targets the wrong env |
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
| ANV-3 | Async database layer and Alembic | Next |
| ANV-4 … ANV-41 | see `backlog.md` | Not started |

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
