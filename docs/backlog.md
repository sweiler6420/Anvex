# Anvex build backlog

The ordered plan for standing up the Anvex monorepo. Each entry is one Linear ticket and one
subagent's unit of work. Tickets execute **sequentially** — each assumes everything above it exists.

Every ticket, without exception:
- follows the layering contract in [`CLAUDE.md`](../CLAUDE.md) §3,
- ships pytest/vitest coverage for what it changed (§6),
- adds any new env var to `.env.example`,
- appends to `CLAUDE.md` only if it establishes a new *framework-level* convention.

Legend — **Est**: rough size (S/M/L). **Dep**: tickets that must land first.

---

## E1 — Foundation

### ANV-1 · Monorepo scaffold and architecture contract · S · dep: —
Create the `Anvex` monorepo: `backend/` + `frontend/` split, `docs/`, `scripts/`, root `.env`,
`.gitignore`, `README.md`, and the `CLAUDE.md` separation-of-duties contract. Backend package
skeleton (`app/{api,clients,data,db,deps,domain,jobs,middleware,models,repos,schemas,services,utils}`)
and `backend/{docs,infra,scripts,tests}`.
**Done when:** repo initialised, first commit on `main`, `CLAUDE.md` describes every layer.

### ANV-2 · Backend uv project and settings · M · dep: ANV-1
`backend/pyproject.toml` managed by uv — runtime deps (fastapi, uvicorn, sqlalchemy[asyncio],
asyncpg, alembic, pydantic-settings, celery[redis], httpx, python-jose, passlib[bcrypt],
python-multipart, aioboto3, structlog) and a `dev` group (pytest, pytest-asyncio, pytest-cov,
respx, ruff, faker). Commit `uv.lock`. Configure ruff + pytest in `pyproject.toml`.
Add `app/settings.py`: a single `pydantic-settings` `Settings` reading the **root** `.env`,
typed, cached with `lru_cache`. Add `__init__.py` to every package folder.
**Tests:** `tests/unit/test_settings.py` — defaults, env override, computed DSNs (Postgres async
DSN, Celery broker/backend URLs).
**Done when:** `uv sync` and `uv run pytest` both succeed.

### ANV-3 · Async database layer and Alembic · M · dep: ANV-2
`app/db/`: async engine (`asyncpg`), `async_sessionmaker`, declarative `Base` bound to the `anvex`
schema, and an async `get_session` context manager. Alembic configured for **async** with
`app/db/migrations/` (`alembic.ini` at `backend/`), `env.py` importing `Base.metadata` and
`app/models`, and a migration that creates the `anvex` schema plus the `pgcrypto` extension.
**Tests:** `tests/unit/test_db.py` — engine builds from settings, session factory yields an
`AsyncSession`, `Base` carries the right schema.
**Done when:** `uv run alembic upgrade head` runs against a local Postgres.

### ANV-4 · App factory, middleware and error contract · M · dep: ANV-3
`app/domain/errors.py` — the exception hierarchy services raise (`AnvexError`, `NotFoundError`,
`ConflictError`, `ValidationError`, `UnauthorizedError`, `ForbiddenError`, `ExternalServiceError`).
`app/middleware/` — request-ID injection, structlog access logging with timing, a handler mapping
domain errors to status codes with a consistent JSON error body, and CORS from settings.
`app/main.py` — `create_app()` factory: lifespan (engine dispose), middleware, `/health` and
`/health/ready`, router mounting. No `create_all`.
**Tests:** `tests/api/test_health.py`, `tests/unit/test_errors.py`, `tests/api/test_middleware.py`
(request ID echoed, domain error → correct status and body shape).
**Done when:** `uv run uvicorn app.main:app` serves `/health` and `/docs`.

### ANV-5 · Docker Compose stack and backend Dockerfile · M · dep: ANV-4
`backend/Dockerfile` — multi-stage, `uv sync --frozen`, non-root user, healthcheck.
Root `docker-compose.yml` — `db` (postgres 16), `db-test`, `redis`, `minio` + `minio-init`
(creates the bucket), `api`, `worker`, `beat`, `web`. All read the root `.env`. Named volumes,
depends_on with healthchecks, hot-reload bind mounts for dev.
**Tests:** `tests/integration/test_compose_health.py` (skipped unless `ANVEX_COMPOSE_TEST=1`) —
hits `/health` and asserts DB + Redis reachability.
**Done when:** `docker compose up` brings the backend up healthy against containerised Postgres.

### ANV-6 · Pytest harness · M · dep: ANV-5
`tests/conftest.py` — event-loop policy, a session-scoped engine against `db-test`, per-test
transaction rollback fixture, an `AsyncClient` fixture over `ASGITransport`, a settings-override
fixture, and a `respx` fixture for client tests. `tests/factories/` — faker-backed builders.
Document the three test tiers and how to run them in `backend/docs/testing.md`.
**Tests:** the harness proves itself — a sample test per tier that exercises each fixture.
**Done when:** `uv run pytest` runs all three tiers green, and rollback isolation is demonstrated.

---

## E2 — Data layer

### ANV-7 · Models and initial migration · M · dep: ANV-6
Port the six tables to SQLAlchemy 2.0 typed declarative (`Mapped` / `mapped_column`) in the
`anvex` schema: `users`, `stocks`, `stock_data`, `watchlists`, `watchlist_data`, `politicians`.
Fixes to carry over deliberately: `watchlist_data` gets a real composite PK (the old model faked
one via `__mapper_args__`), `stocks.ticker_symbol` widened past 5 chars, timestamps become
timezone-aware with server defaults, and `stock_data` gets a `(stock_id, date, time)` unique
constraint plus an index for range queries. Autogenerate the initial Alembic migration.
**Tests:** `tests/integration/test_models.py` — round-trip each model, constraint violations
raise, relationships resolve, and `upgrade`→`downgrade`→`upgrade` is clean.
**Done when:** migration applies to an empty DB and matches `Base.metadata`.

### ANV-8 · Pydantic schemas · S · dep: ANV-7
`app/schemas/` — pydantic v2 contracts per resource, split `XCreate`/`XUpdate`/`XOut`, with
`ConfigDict(from_attributes=True)`. Includes auth token schemas, a generic `Page[T]` envelope,
and the shared error-body schema from ANV-4. Never expose `users.password`.
**Tests:** `tests/unit/test_schemas.py` — validation rules, ORM-mode conversion, password never
serialised, email validation, `Page` generics.

### ANV-9 · Repositories · L · dep: ANV-8
`app/repos/` — `UserRepo`, `StockRepo`, `StockDataRepo`, `WatchlistRepo`, `PoliticianRepo`, over a
small `BaseRepo`. Every method is `async`, takes `AsyncSession`, and **does not commit**. Covers
everything the old routers queried: user lookup by email *or* username, stock by id and by
ticker, paginated stock-data range queries with ticker search, watchlist with ordered stock join,
and bulk upsert for ingest.
**Tests:** `tests/integration/test_repos_*.py` — one module per repo, real Postgres, rollback
isolation, including pagination boundaries and the ordered watchlist join.

---

## E3 — Auth

### ANV-10 · Security utilities and pure token domain · M · dep: ANV-9
`app/utils/security.py` — bcrypt hash/verify via passlib (generic, no Anvex meaning).
`app/domain/auth.py` — **pure** token logic: build access/refresh claim sets from a user id and an
injected `now`, encode/decode with an injected secret, and classify expiry vs. malformed vs. wrong
token type. No FastAPI imports, no settings reads, no `datetime.utcnow()` inside.
**Tests:** `tests/unit/test_security.py`, `tests/unit/test_domain_auth.py` — hash round-trip,
wrong password fails, expired token detected via injected clock, tampered signature rejected,
refresh token rejected where an access token is required.

### ANV-11 · Auth service, dependencies and routes · L · dep: ANV-10
`app/services/auth.py` — `login` (accepts email *or* username, as today), `refresh`, `recovery`.
`app/deps/` — `get_session`, `get_current_user` (`OAuth2PasswordBearer` on `/v1/auth/login`),
`get_settings`, service factories. `app/api/v1/auth.py` — `POST /v1/auth/login`
(`OAuth2PasswordRequestForm`), `POST /v1/auth/refresh`, `POST /v1/auth/recovery`.
Keeps the old response shape `{access_token, refresh_token, token_type}` so the frontend contract
holds. The old `/v1/refresh` took the token as a *query param* — the new endpoint takes a JSON body.
**Tests:** `tests/unit/` for service logic with fake repos; `tests/api/test_auth.py` for login
success/failure, refresh rotation, expired-token 401, and that protected routes reject anonymous
callers.

### ANV-12 · Users service and routes · M · dep: ANV-11
`app/services/user.py` — registration with duplicate email/username conflicts, password hashing,
and current-user lookup. `app/api/v1/users.py` — `POST /v1/users` (register),
`GET /v1/users/me`, `GET /v1/users/{user_id}`. Fixes the old router's broken paths (`'{id}'` with
no leading slash) and its param/path mismatch.
**Tests:** registration happy path, duplicate email → 409, duplicate username → 409, weak-input
validation → 422, `/me` requires auth, password absent from every response.

---

## E4 — Core features

### ANV-13 · Stocks service and routes · M · dep: ANV-12
`app/services/stock.py` + `app/api/v1/stocks.py` — `GET /v1/stocks` (paginated, ticker/company
search), `GET /v1/stocks/{stock_id}`, `GET /v1/stocks/by-ticker/{ticker}`.
**Tests:** unit service tests with fake repos; API tests for 200/404/auth and pagination.

### ANV-14 · Stock data service and routes · M · dep: ANV-13
`app/services/stock_data.py` + `app/api/v1/stock_data.py` — `GET /v1/stock-data` with ticker
filter, date range, limit/offset, returning the combined `datetime` field the charts expect.
Pure paging/range validation lives in `app/domain/stock_data.py`.
**Tests:** unit tests for range/paging rules; integration tests over seeded candles; API contract
test asserting the `datetime` field shape the frontend charts consume.

### ANV-15 · Watchlists — reorder domain, service and routes · L · dep: ANV-14
`app/domain/watchlist.py` — **pure** reordering: `reposition(items, stock_id, from_idx, to_idx)`
returning new positions. The old implementation was index-vs-position confused and had an
assumes `position == index`; write this as a clean pure function and test it exhaustively.

The shift arithmetic itself is **correct** while positions are exactly `0..n-1` — do not go hunting
for an off-by-one. What is actually wrong with the old handler:

1. **No ownership check.** It filters `WatchlistData` by `watchlist_id` alone. `current_user` is
   injected and never used, so any authenticated caller can reorder anybody's watchlist.
2. **Unvalidated client indices.** `current_index` / `destination_index` arrive from the query
   string and are used directly as list subscripts — out of range is an `IndexError` (a 500), and a
   negative index silently wraps under Python semantics and moves the wrong row.
3. **`stock_id` is accepted and never used.** The row that moves is whichever sits at
   `current_index`, so a stale client view silently reorders the wrong stock.
4. **It indexes a position-ordered list by index**, so it holds only while positions are contiguous
   from zero. Nothing enforces that — ANV-7 deliberately left `position` non-unique.
`app/services/watchlist.py` + `app/api/v1/watchlists.py` — create, list mine, get with ordered
stocks, add stock (409 on duplicate), remove stock, reorder, delete. Every route enforces
ownership by `current_user`.
**Tests:** exhaustive unit tests for `reposition` (move up, move down, no-op, first, last,
single-item, invalid index); API tests for ownership isolation (user B cannot read user A's
watchlist) and duplicate-add conflict.

### ANV-16 · Politicians seed data, service and routes · M · dep: ANV-15
`app/data/politicians.json` + a loader in `app/data/`. `app/services/politician.py`,
`app/api/v1/politicians.py` — `GET /v1/politicians` (filter by state/party/chamber, paginated),
`GET /v1/politicians/{id}`. A `backend/scripts/seed_politicians.py` entry point that calls the
service.
**Tests:** loader parses fixture data, filters work, seed is idempotent on re-run.

---

## E5 — Integrations and background jobs

### ANV-17 · Client base · M · dep: ANV-16
`app/clients/base.py` — shared async HTTP client: `httpx.AsyncClient` lifecycle, timeouts,
bounded retry with backoff on 5xx/network errors, rate-limit awareness, structured request
logging, and normalisation of vendor failures into `ExternalServiceError`. Documents the client
contract (primitives in, typed data out, zero Anvex knowledge).
**Tests:** `tests/integration/test_client_base.py` with `respx` — retry on 500 then succeed, give
up after N attempts, timeout mapped to `ExternalServiceError`, 4xx not retried.

### ANV-18 · AlphaVantage client · M · dep: ANV-17
`app/clients/alphavantage.py` — `fetch_intraday(symbol, interval, month)` returning typed candles.
Ports the parsing from the old `AverageInvestorService` ETL (the `1. open`/`2. high`… key mapping,
numeric coercion, 2dp rounding, datetime split into date + time) but as pure typed parsing with
**no pandas** and no DB knowledge.
**Tests:** `respx`-mocked fixtures of real AlphaVantage payload shapes — happy path, rate-limit
note response, empty series, malformed payload.

### ANV-19 · NewsAPI client, service and routes · M · dep: ANV-18
`app/clients/newsapi.py` — real `top-headlines` / `everything` calls, replacing the hardcoded
JSON blob the old `news.py` router returned. `app/services/news.py`, `app/api/v1/news.py` —
`GET /v1/news/top`, `GET /v1/news/by-symbol/{ticker}`. Any ranking/dedupe rule is pure and lives
in `app/domain/news.py`.
**Tests:** `respx` client tests; unit tests for dedupe/ranking; API tests with the service stubbed.

### ANV-20 · S3 client and storage service · M · dep: ANV-19
`app/clients/s3.py` — `aioboto3` wrapper (put, get, presigned URL, exists, delete) pointed at
MinIO locally and real S3 in AWS via settings. `app/services/storage.py` for Anvex-meaningful
operations (export naming, content types).
**Tests:** integration tests against the compose MinIO container, skipped when unavailable;
unit tests for key construction and presign parameters.

### ANV-21 · Celery application and worker wiring · M · dep: ANV-20
`app/jobs/celery_app.py` — Celery configured from settings (Redis broker + result backend), task
discovery, serialisation, sane retry/ack defaults, and a beat schedule. A `ping` health task.
Compose `worker` and `beat` services proved working. Documents in `CLAUDE.md` how a task resolves
an async service from sync Celery context.
**Tests:** task registration, eager-mode execution of `ping`, beat schedule shape.

### ANV-22 · Stock ingest job · L · dep: ANV-21
The `AverageInvestorService` ETL, reborn correctly. `app/domain/ingest.py` — **pure** windowing
rules: which months to request, the 08:05–17:00 trading-hours filter, and which fetched candles
are genuinely new given what the DB already has. `app/services/ingest.py` — orchestrates
`AlphaVantageClient` → domain filter → `StockDataRepo.bulk_upsert`. `app/jobs/ingest.py` — a
scheduled Celery task per tracked ticker, idempotent and retry-safe.
**Tests:** exhaustive unit tests for the windowing and dedupe rules (the old code's most bug-prone
area); integration test of the full service with a mocked client against a real DB, run twice to
prove idempotency.

---

## E6 — Frontend foundation

### ANV-23 · Vite scaffold, Tailwind and test harness · L · dep: ANV-22
`frontend/` — Vite + React 18, `package.json`, `vite.config.js` (path aliases, dev proxy to the
API, `VITE_`-prefixed env), Tailwind carried over verbatim from the old app (brand=cyan,
neutral=slate, the custom font scale, `RTFont`/Poppins, class-based dark mode), `index.html`,
static assets and fonts copied from `AverageInvestorWeb/public` and `src/assets`.
`frontend/Dockerfile` (dev + build stages) and the compose `web` service.
Vitest + Testing Library + MSW + jsdom configured with a sample passing test.
**Done when:** `docker compose up web` serves the app and `npm run test` passes in-container.

### ANV-24 · API client layer · M · dep: ANV-23
`src/lib/config.js` (typed env access), `src/lib/api/client.js` — public and authenticated axios
instances, request interceptor attaching the bearer token, response interceptor performing a
**single-flight** refresh on 401 and replaying queued requests. Replaces the old `useApi` grab-bag
with per-resource modules under each feature's `api.js`.
**Tests:** MSW-driven tests for token attachment, refresh-on-401 replay, concurrent-request
single-flight (only one refresh call), and refresh failure clearing auth.

### ANV-25 · Theme and error providers · S · dep: ANV-24
`src/providers/ThemeProvider.jsx` (class-based dark mode, `localStorage` persistence, guarded
against unavailable storage) and `ErrorsProvider.jsx` (transient error surface with auto-clear).
Ported from the old app, with the `useDarkMode`/`useErrors` hooks.
**Tests:** theme toggles the root class and persists; error auto-clears on its timer.

### ANV-26 · Auth state and token lifecycle · M · dep: ANV-25
**Corrected 2026-08-29:** this entry said `src/features/auth/`, but `CLAUDE.md` §5's layout names
`providers/` as the home of the auth context, and `useAuth` is consumed by the router, the header
and the login page — a cross-feature hook by definition. It shipped as
`providers/AuthContext.js` + `providers/AuthProvider.jsx` + `hooks/useAuth.js`, with
`features/auth/` keeping what §5 actually assigns to a feature: `api.js` and the storage policy.

Auth store: access token **in memory**, refresh token in `localStorage`,
plus `login`, `logout`, `restore` (silent refresh on boot) and `useAuth`. Replaces the old
`AuthProvider` + `PersistLogin` + `useRefreshToken` trio.
**Tests:** login stores tokens, logout clears both, boot-time restore succeeds and fails cleanly,
token never leaks to `localStorage`.

### ANV-27 · TanStack Router and route guards · L · dep: ANV-26
`@tanstack/react-router` — router instance with auth context, a root route, and the tree:
`/`, `/login`, `/signup`, `/recovery`, `/unauthorized`, `/research`, `/portfolio`. Protected
routes guard in `beforeLoad` by redirecting to `/login` with a `redirect` search param, and
`/login` bounces authenticated users onward — reproducing the old `RequireAuth` + `PersistLogin`
behaviour (including "return to where you came from") without wrapper components. Pending
component covers the silent-refresh boot window.
**Tests:** guard redirects when anonymous, allows when authenticated, preserves and honours the
`redirect` param, and public routes stay reachable.

### ANV-28 · Layout, header and dark-mode switcher · M · dep: ANV-27
`src/components/layout/` — `Layout`, `Header` (responsive nav, mobile drawer, auth-aware links,
logo) and `DarkModeSwitcher`, ported from the old app and wired to the router's `Outlet`.
**Tests:** nav renders auth-appropriate links, mobile menu toggles, switcher flips the theme.

---

## E7 — Frontend pages

### ANV-29 · Login page · M · dep: ANV-28
`src/features/auth/components/LoginPage.jsx` + route. Must preserve today's behaviour exactly:
username-or-email field, password visibility toggle, "remember me" prefill, per-field validation
messages, prefill from the sign-up hand-off, error banner with "Try Again" button text, and
redirect to `/research` (or the guarded origin) on success.
**Tests:** validation blocks empty submits, successful login redirects, failed login surfaces the
server message, remember-me persists, visibility toggle works.

### ANV-30 · Sign-up page · M · dep: ANV-29
Ported sign-up: email format validation, username rules (7+ chars, not equal to the email),
strong-password rules with the hover tooltip listing them, and the navigate-to-login hand-off
carrying the credentials. Rebuilt on the new API layer.
**Tests:** each validation rule, duplicate-email server error surfaces, success hands off to login.

### ANV-31 · Recovery and Unauthorized pages · S · dep: ANV-30
Ported password recovery (submit, success message, 3s redirect to login, error clearing on
unmount) and the Unauthorized page.
**Tests:** submit success shows confirmation and redirects; validation and error paths.

### ANV-32 · Home marketing page · L · dep: ANV-31
Port `Hero`, `Features`, `Workflow`, `Pricing`, `Contact`, `Footer`, `Who`, `Works` and the `Home`
composition. Copy, layout, gradients and the existing Anvex branding stay identical; only imports
and routing links change.
**Tests:** each section renders its key copy; CTAs link to `/signup` and `#features`.

### ANV-33 · Bin-packing window system · L · dep: ANV-32
Port `GridManager`, `algorithms/skyline`, `WindowManager`, `Window`, `WindowMenu` and
`BinPackingLayout` (~1200 lines) into `src/features/desktop/`. Behaviour-preserving port —
drag, resize, snap, collapse, fullscreen and packing must work as they do today. The pure
algorithm modules stay pure and framework-free.
**Tests:** heavy unit coverage of the skyline packer and `GridManager` geometry (placement,
collision, free-space search, edge cases); component tests for window drag/resize/close.

### ANV-34 · Dashboard widgets · M · dep: ANV-33
Port `StockChart`, `LineChart`, `Watchlist`, `CounterWidget`, `StaticInfoWidget`,
`TextInputWidget` and `widgets/utils.js`, rewired to the new API layer and the d3 deps.
**Tests:** `utils.js` pure functions; each widget renders with mocked data; the Watchlist widget's
reorder calls the API.

### ANV-35 · Interactive desktop demo · M · dep: ANV-34
Port `InteractiveDesktop` and mount it in the Home page as it is today, composed from ANV-33 and
ANV-34.
**Tests:** renders the default window set; adding a widget from the menu mounts it.

### ANV-36 · Research and Portfolio pages · M · dep: ANV-35
The two authenticated routes. Port their current placeholder content faithfully, then mount the
bin-packing desktop on `/research` as the working surface, backed by the real stocks, stock-data
and watchlist endpoints.
**Tests:** both routes require auth, render for an authenticated user, and load their data.

---

## E8 — Operations, CI and documentation

### ANV-37 · Developer scripts · M · dep: ANV-36
`scripts/` in both PowerShell and sh: `up`, `down`, `logs`, `migrate`, `makemigration`, `seed`,
`test` (backend + frontend), `lint`, `fmt`, and `reset-db`. `backend/scripts/` keeps
backend-only entry points. Documented in `README.md`.
**Tests:** a smoke test asserting every script exists and is executable/parseable.

### ANV-38 · CI pipeline · M · dep: ANV-37
`.github/workflows/ci.yml` — backend job (uv sync, ruff, pytest with a Postgres + Redis service
container, coverage) and frontend job (npm ci, lint, vitest, build). Runs on push and PR, with
path filters so each stack only builds when it changes.
**Done when:** the workflow is green on a pushed branch.

### ANV-39 · Documentation and ADRs · M · dep: ANV-38
`docs/architecture.md` (system diagram, request path, job path, data model), `backend/docs/`
(runbook, testing guide, adding-an-endpoint walkthrough), and ADRs for the decisions already made:
monorepo over three repos, async-first, uv, layered backend, TanStack Router, Postgres + S3.

### ANV-40 · AWS infrastructure skeleton · L · dep: ANV-39
`backend/infra/` — Terraform skeleton for the eventual AWS target: VPC, RDS Postgres, ElastiCache
Redis, S3 bucket, ECR, ECS Fargate services for api/worker/beat, ALB, and Secrets Manager wiring,
split into modules with a `local`/`dev` var layout. **Not applied** — this ticket produces
reviewable, `terraform validate`-clean configuration only. Local development must never depend on it.
**Done when:** `terraform init && terraform validate` passes and `docs/` explains the intended
deploy path.

### ANV-41 · End-to-end smoke verification · M · dep: ANV-40
A scripted, documented full-stack pass: `docker compose up` → migrate → seed → register a user →
log in from the web app → load `/research` → read watchlist and stock data → trigger an ingest
task and observe it complete. Captured as `scripts/smoke.ps1` plus a `docs/smoke.md` checklist.
**Done when:** the whole stack boots clean from `git clone` + `cp .env.example .env` and the smoke
script passes end to end.

---

## Deliberate fixes carried in

Things the old code got wrong that are corrected as part of the port, rather than faithfully
reproduced:

| Where | Old behaviour | New behaviour | Ticket |
| --- | --- | --- | --- |
| `watchlist.reposition` | no ownership check; unvalidated client indices used as list subscripts; `stock_id` ignored | pure, exhaustively tested `reposition` keyed on `stock_id`, ownership enforced | ANV-15 |
| `watchlist_data` | fake PK via `__mapper_args__` | real composite primary key | ANV-7 |
| `users` router | paths `'{id}'` with no slash; `get_user` declared `id` but read `user_id` | correct paths and params, plus `/me` | ANV-12 |
| `stocks.ticker_symbol` | `VARCHAR(5)` | widened; many real tickers exceed 5 chars | ANV-7 |
| `news` router | hardcoded 2023 JSON blob | real NewsAPI client behind a service | ANV-19 |
| `/v1/refresh` | refresh token passed as a query parameter | JSON request body | ANV-11 |
| config | `settings/config.{env}.json` + commented-out Secrets Manager | one root `.env` via pydantic-settings | ANV-2 |
| DB access | sync `Session`, `create_all` at import time | async sessions, Alembic-only schema | ANV-3, ANV-7 |
| ETL | pandas script on EC2 with `df.append` (removed in pandas 2) | pure parsing + Celery task | ANV-18, ANV-22 |
