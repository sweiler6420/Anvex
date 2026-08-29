# Anvex

Investment research platform. Monorepo: async FastAPI + Celery backend, Vite/React frontend,
Postgres + S3 data stores. Runs locally today via Docker Compose; AWS is the eventual target.

> This file is the architectural contract for the repo. Every agent working in Anvex reads it
> first and appends to it when they establish a new framework-level convention. Keep it accurate
> — a stale CLAUDE.md is worse than no CLAUDE.md.

---

## 1. Repository layout

```
Anvex/
├── .env                  # single source of runtime config for ALL stacks. Gitignored.
├── .env.example          # committed template. Every new var MUST be added here.
├── CLAUDE.md             # this file
├── docker-compose.yml    # local dev: postgres, redis, minio, api, worker, beat, web
├── docs/                 # cross-stack docs; docs/adr/ holds architecture decision records
├── scripts/              # repo-wide dev scripts (up, down, migrate, seed, test, lint)
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml    # uv-managed. NO requirements.txt anywhere.
│   ├── uv.lock           # committed
│   ├── alembic.ini
│   ├── app/              # the application package (see §3)
│   ├── docs/             # backend-specific docs
│   ├── infra/            # AWS IaC (terraform). Local dev never depends on this.
│   ├── scripts/          # backend-only scripts
│   └── tests/            # pytest suite, mirrors app/ (see §6)
└── frontend/
    ├── Dockerfile
    ├── package.json
    ├── vite.config.js
    └── src/              # see §5
```

Anything that does not fit one of these homes does not get a new top-level folder without an ADR
in `docs/adr/`.

---

## 2. Non-negotiables

- **Async everywhere in the backend.** Route handlers, services, repos, clients, and DB access are
  `async def`. SQLAlchemy 2.0 async ORM with `asyncpg`. No `Session`, no `create_engine`, no
  blocking `requests`. Use `httpx.AsyncClient`.
- **uv only.** `uv add` / `uv sync` / `uv run`. Never `pip install`, never a `requirements.txt`.
- **One `.env` at the repo root.** Backend, worker, and frontend all read from it (compose injects
  it). Never add a second env file per stack. Add every new key to `.env.example` in the same commit.
- **Tests ship with the change.** A ticket is not done until its code has tests and the suite is green.
- **Modularity is the point.** If you are unsure where code goes, re-read §3. Putting logic in the
  wrong layer is a defect even if the feature works.

---

## 3. Backend layering — separation of duties

This is the core discipline of the repo. Each folder under `backend/app/` has exactly one job.
Dependencies flow **downward only**; a layer may import from layers below it, never above.

```
api  ->  services  ->  domain          (domain is pure: no I/O)
              |    \
              |     ->  clients        (third-party / network)
              v
            repos                      (the only place SQLAlchemy queries live)
              |
              v
          db / models
```

### `app/api/` — HTTP surface
FastAPI routers only. A handler's entire job is: accept a validated request, call **one** service,
return a schema. Versioned as `app/api/v1/<resource>.py`, aggregated by `app/api/v1/__init__.py`.

- **Allowed:** `APIRouter`, path/query/body params, `Depends`, status codes, `response_model`.
- **Forbidden:** SQLAlchemy queries, `httpx` calls, business rules, `if`/`for` beyond trivial
  request shaping. If a handler is longer than ~15 lines, logic leaked in — push it to a service.
- Handlers raise nothing but `HTTPException`; services raise domain exceptions that middleware maps.

### `app/clients/` — third-party I/O
**Every** external API, SDK, or network boundary gets a client class here. One module per provider:
`alphavantage.py`, `newsapi.py`, `s3.py`.

- A client knows about *one* vendor: its base URL, auth, retries, rate limits, and response shape.
- A client returns typed data (a pydantic model or plain dict), never a raw `Response` object.
- A client knows **nothing** about Anvex — no DB, no repos, no Anvex domain concepts, no
  `stock_id`. It takes primitives and gives back the vendor's data.
- All clients subclass the shared base in `app/clients/base.py` for timeout/retry/logging behavior.
- **Rule of thumb: if it makes a network call to something we do not own, it is a client.**

### `app/data/` — static and seed data
Checked-in reference data (JSON/CSV) and the loaders that read it: politician rosters, exchange
lists, ticker seeds. No network calls, no DB writes — loaders return parsed structures for a
service or a job to persist.

### `app/db/` — connection plumbing
Async engine, `async_sessionmaker`, declarative `Base`, session lifecycle, and the Alembic
`migrations/` tree. Nothing here knows what a Stock is.

### `app/deps/` — FastAPI dependencies
Reusable `Depends` providers: `get_session`, `get_current_user`, pagination params, rate-limit
guards, client/service factories. Dependencies wire objects together; they do not implement logic.

### `app/domain/` — Anvex business logic
**Pure functions and pure classes.** This is where Anvex's actual rules live: watchlist reordering,
position math, indicator calculation, token claim construction, ingest windowing rules.

- Takes plain data in, returns plain data out. **No I/O of any kind** — no DB, no HTTP, no clock
  reads (pass `now` in), no env reads.
- Because it is pure, it is the cheapest and most valuable thing to unit-test. Test it exhaustively.
- **Rule of thumb: if a rule would still be true on paper without a computer, it belongs in domain.**

### `app/jobs/` — Celery tasks
The Celery app, task definitions, and beat schedule. A task is a thin entrypoint that resolves its
dependencies and calls **one** service — the same shape as an API handler. Business logic never
lives in a task body. Tasks are idempotent and safe to retry.

### `app/middleware/` — cross-cutting request concerns
Request ID injection, structured access logging, timing, exception-to-HTTP mapping, CORS wiring.
Applies to every request; never resource-specific.

### `app/models/` — SQLAlchemy ORM
Declarative table definitions and relationships. Persistence shape only. **No methods containing
business logic** — that is `domain/`. One module per table group; export via `models/__init__.py`
so Alembic autogenerate sees everything.

### `app/repos/` — data access
The **only** place SQLAlchemy queries are written. One repo class per aggregate
(`UserRepo`, `StockRepo`, `WatchlistRepo`). Each method takes an `AsyncSession` and returns models
or scalars.

- A repo does not commit — the caller (service) owns the transaction boundary.
- A repo contains no business rules, no HTTP concepts, no `HTTPException`.
- **If you typed `select(` outside `app/repos/`, it is in the wrong file.**

### `app/schemas/` — pydantic contracts
Request and response models, split as `XCreate` / `XUpdate` / `XOut`. Pydantic v2
(`model_config = ConfigDict(from_attributes=True)`). Schemas are the API's public shape — never
return an ORM model directly and never accept one as a request body.

### `app/services/` — orchestration
The layer that gets things done. A service coordinates repos, clients, and domain functions to
fulfil a use case, and owns the transaction and the error semantics.

- **This is the only layer allowed to talk to more than one other layer.**
- Services raise domain exceptions (`NotFoundError`, `ConflictError`), never `HTTPException`.
- **Rule of thumb: a service is the answer to "what does the app *do*", composed from the answers
  to "how do we store it" (repo), "how do we fetch it" (client), and "what are the rules" (domain).**

### `app/utils/` — generic helpers
Framework-agnostic utilities with no Anvex meaning: datetime coercion, pagination math, string
normalization, password hashing primitives. If a helper mentions a Anvex concept, it is domain.

### Worked example — adding "sync a stock's news"
1. `clients/newsapi.py` → `NewsApiClient.fetch_headlines(symbol)` — vendor call only.
2. `domain/news.py` → `dedupe_and_rank(articles, now)` — pure ranking rule.
3. `repos/news.py` → `NewsRepo.bulk_upsert(session, rows)` — the only `select`/`insert`.
4. `services/news.py` → `NewsService.sync_for_stock(symbol)` — calls client, calls domain, calls
   repo, commits.
5. `api/v1/news.py` → `POST /v1/news/sync` — one call to `NewsService`.
6. `jobs/news.py` → nightly Celery task calling the same `NewsService` method.
7. Tests at every layer (§6).

Note that steps 5 and 6 both reuse step 4. **That reuse is the whole reason for the layering.**

---

## 4. Backend conventions

- **Routes:** `/v1/<plural-resource>`. Router `prefix` carries the version; never hardcode it in a
  path decorator.
- **IDs:** UUID primary keys, `gen_random_uuid()` server default.
- **DB schema:** all tables live in the `anvex` Postgres schema.
- **Migrations:** Alembic, async. Every model change ships with a migration in the same commit.
  Never use `Base.metadata.create_all` outside tests.
- **Errors:** services raise from `app/domain/errors.py`; middleware maps them to status codes.
- **Config:** `pydantic-settings` `Settings` object in `app/settings.py`, read once, injected via
  `deps`. Never `os.getenv` outside that module.
- **Logging:** structured, request-id-tagged. No bare `print`.
- **Auth:** JWT access + refresh (`python-jose`), bcrypt password hashing, `OAuth2PasswordBearer`.

---

## 5. Frontend conventions

Vite + React 18 + Tailwind + **TanStack Router**. Kept modular so it can later be wrapped for
iOS/Android.

```
frontend/src/
├── routes/       # TanStack Router route modules. Route = layout + data loading + guard only.
├── features/     # one folder per domain area (auth, watchlist, research, portfolio):
│                 #   components/, hooks/, api.js  — feature-local, not shared
├── components/   # genuinely shared presentational components (ui/, layout/)
├── lib/          # api client, axios instances + interceptors, router config, env config
├── hooks/        # cross-feature hooks only
├── providers/    # React context providers (auth, theme, errors)
└── styles/
```

- **Routes are thin.** A route file declares the route, its `beforeLoad` auth guard, and renders a
  feature component. Business logic lives in `features/`.
- **Auth guarding happens in the router**, via `beforeLoad` redirects — not by rendering a
  `<RequireAuth>` wrapper. Access token in memory, refresh token in `localStorage`.
- **All network calls go through `lib/api`.** No bare `axios`/`fetch` inside a component.
- **Feature-first.** Code used by one feature lives in that feature. Promote to `components/` or
  `hooks/` only on the second real consumer.
- Tailwind config, brand palette (`brand`=cyan, `neutral`=slate), `RTFont`/Poppins fonts, and
  class-based dark mode are carried over from the previous web app and must keep working.

---

## 6. Testing

**Every ticket adds tests for what it changed.** No exceptions.

### Backend — pytest
- `pytest`, `pytest-asyncio` (`asyncio_mode = "auto"`), `httpx.AsyncClient` +
  `ASGITransport` for API tests. Coverage via `pytest-cov`.
- Layout mirrors the app:
  - `tests/unit/` — **domain and utils. Pure, fast, no fixtures, no I/O.** This is where the bulk
    of the tests live. Cover the edge cases here, not through the API.
  - `tests/integration/` — repos and services against a real Postgres (compose `db-test`), each
    test in a rolled-back transaction. Clients are tested with `respx`-mocked HTTP; never hit a
    live vendor API in a test.
  - `tests/api/` — route contract tests: status codes, response shape, auth enforcement,
    validation errors. Services are stubbed via dependency overrides.
- Shared fixtures in `tests/conftest.py`; model factories in `tests/factories/`.
- Run: `uv run pytest` (or `scripts/test.ps1`).

### Frontend — vitest
- `vitest` + `@testing-library/react` + `msw` for network mocking.
- Colocated as `*.test.jsx` beside the unit under test.
- Cover: auth flow (login/refresh/logout/guard redirects), route guards, forms and their
  validation, and any non-trivial pure logic (bin-packing algorithms, widget math).

---

## 7. Working agreement for agents

1. Read this file before writing code. Follow §3 literally.
2. Stay in your ticket's scope. Do not refactor neighbouring layers opportunistically.
3. Add tests in the same change. Run the suite. Report real results — if something fails, say so.
4. Add every new env var to `.env.example`.
5. If you establish a new framework-level convention (a base class, an error contract, a fixture
   pattern, a router pattern), append it to this file. Do not document ticket-specific detail here.
6. Prefer extending an existing module over creating a parallel one.

---

## 8. Provenance

Anvex replaces three earlier repos: `AverageInvestorApi` (sync FastAPI), `AverageInvestorService`
(standalone AlphaVantage ETL for EC2/Lambda), and `AverageInvestorWeb` (CRA React). The service
repo's concept is deliberately gone — its ETL now lives as Celery jobs in `backend/app/jobs/`
backed by a client in `backend/app/clients/`. Those three repos are read-only history and are
never modified.
