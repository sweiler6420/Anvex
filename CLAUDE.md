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

- **SQLAlchemy 2.0 typed declarative only**: `Mapped[...]` annotations with `mapped_column(...)`.
  No `Column()`, no `__mapper_args__` standing in for a real key.
- Subclass `app.db.base.Base` and nothing else. `Base.metadata` already carries
  `schema="anvex"`, so **never** set `__table_args__ = {"schema": ...}` on a model.
- **Every foreign key states its `ondelete`.** Omitting it is a decision by default: the
  dependent rows simply block the parent's deletion. Pick `CASCADE` when the child is part of
  the parent (a candle, a watchlist, a membership row) and `RESTRICT` when the parent is
  reference data somebody depends on. Mirror it on the relationship with
  `passive_deletes=True` (DB-side cascade) or `passive_deletes="all"` (DB-side restrict), or
  the ORM will load the children and try to rewrite them first.
- Timestamps are `DateTime(timezone=True)` with a server default. Never a naive `TIMESTAMP`.
- Type annotations tell the truth about what comes back: `Mapped[Decimal]` for `Numeric`,
  `Mapped[uuid.UUID]` for a UUID key, `Mapped[X | None]` exactly when the column is nullable.
  ANV-8's schemas are generated against these, so a lie here becomes a lie in the API.

### `app/repos/` — data access
The **only** place SQLAlchemy queries are written. One repo class per aggregate
(`UserRepo`, `StockRepo`, `WatchlistRepo`). Each method takes an `AsyncSession` and returns models
or scalars.

- A repo does not commit — the caller (service) owns the transaction boundary.
- A repo contains no business rules, no HTTP concepts, no `HTTPException`.
- **If you typed `select(` outside `app/repos/`, it is in the wrong file.**
- **A repo takes a session; it never holds one.** Every method's first argument is the
  `AsyncSession`, so a repo instance is stateless and shareable — each module exports a
  ready-made one (`user_repo`, `stock_repo`, …) beside its class. A repo that stored a
  session would have to be constructed per request and could outlive the transaction it
  captured.
- **Every repo subclasses `app.repos.base.BaseRepo[Model]`**, which owns add/update/delete
  (each ending in `flush()`, never `commit()`) and the `_one_or_none` / `_all` / `_count` /
  `_page` / `_exists` query helpers. Do not re-derive pagination or an existence check.
- **Method naming is uniform across the package:** `get_*` returns one model or `None`;
  `list_*` returns `(rows, total)` when paginated and `list[Model]` when not; `count_*` and
  `max_*` return scalars; `*_exists` returns `bool`; `create*`/`add_*` insert and flush;
  `update`/`set_*` mutate and flush; `delete*`/`remove_*` delete and flush; `bulk_upsert`
  is `INSERT ... ON CONFLICT DO UPDATE` returning a row count.
- **A paginated repo method returns `(rows, total)`, not a `Page`.** `total` is counted
  before the window, so an offset past the end yields `([], total)`. Building the envelope
  is the service's job — a repo does not import `app.schemas`, which is also why `limit` is
  a required keyword argument rather than defaulting to `DEFAULT_PAGE_LIMIT`.
- **Every relationship a caller will touch is named in `.options(selectinload(...))`** by
  the query that loads it; lazy loading raises `MissingGreenlet` under asyncio. Where the
  eager load is optional it is a *separate method* (`get_by_id` vs `get_with_entries`),
  never a boolean flag.
- **Idempotent bulk writes are `INSERT ... ON CONFLICT DO UPDATE` on a real constraint**,
  never read-then-write. The caller deduplicates the batch first (Postgres rejects a
  statement that hits one conflict target twice) and re-reads afterwards if it still holds
  ORM objects — a Core statement does not update the identity map.
- **A uniqueness check takes an `exclude_*_id`**, so the same method serves a create ("is
  it taken") and an update ("is it taken by somebody else").

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

### The service / dependency / handler shape

Established by `app/services/auth.py`, `app/deps/auth.py` and `app/api/v1/auth.py` (ANV-11).
**Every resource after it is written to this shape**; if a new one deviates, it is the new one
that is wrong.

**A service** is a class named `XService` in `app/services/x.py`, constructed with its
collaborators and never with a request:

```python
class XService:
    def __init__(self, session: AsyncSession, settings: Settings, *, xs: XRepo = x_repo) -> None:
```

Repos arrive as keyword arguments defaulting to the module-level singletons — that default is
what lets a unit test pass an in-memory fake and never reach Postgres. One `async` method per
use case, keyword-only arguments, returning a **schema** (or a model another service consumes);
never an ORM row straight to the API. It raises `app.domain.errors` exceptions and owns the
`commit()`. It is also the **only** layer allowed to read a clock (`datetime.now(UTC)`, once at
the top of a method, passed down) or to unwrap a `SecretStr` (`.get_secret_value()`).

**A dependency** wires and nothing else. `app/deps/x.py` exports a `get_x_service` factory that
resolves `get_session` + `get_settings_dep` and constructs the service, plus an
`XServiceDep = Annotated[XService, Depends(get_x_service)]` alias. That factory is the **one
seam** an API test overrides, which is why every resource has exactly one. Logic that looks like
it belongs in a dependency (decode this token, then load that user) belongs in the service, so a
Celery task can do it too — `get_current_user` is four lines and delegates.

**A handler** accepts a validated request, calls **one** service method, returns a schema. No
`try`, no `if`, no session, no `HTTPException` — the middleware maps the domain error. Over ~15
lines means logic leaked in. The resource module owns `prefix="/x"` and `tags`; `app/api/v1/
__init__.py` adds the two-line include.

**Protected routes** annotate `user: CurrentUser` (from `app/deps/`). Ownership is checked in the
service against `user.user_id` — repos deliberately provide no "owned by" query, because
authorization is not data access.

---

## 4. Backend conventions

- **Routes:** `/v1/<plural-resource>`. Router `prefix` carries the version; never hardcode it in a
  path decorator.
- **IDs:** UUID primary keys, `gen_random_uuid()` server default.
- **DB schema:** all tables live in the `anvex` Postgres schema.
- **Migrations:** Alembic, async. Every model change ships with a migration in the same commit.
  Never use `Base.metadata.create_all` outside tests. `backend/alembic.ini` + `app/db/migrations/`;
  `env.py` is async and reads the URL from `get_settings()`, never from `alembic.ini`, and alembic's
  own `alembic_version` table lives in the `anvex` schema with everything else.
- **`alembic check` must report "No new upgrade operations detected."** That is the contract
  between `app/models/` and `app/db/migrations/`: a hand-edited migration that has drifted from
  the models fails it, and the test suite asserts it too. Autogenerate output is a draft —
  review and reformat it, but never change what it *does* without re-running the check.
- **Alembic's connection pins `search_path` to `public`** (`ENGINE_CONNECT_ARGS` in `env.py`).
  The login role is also called `anvex`, so Postgres' stock `"$user", public` search path made
  `anvex` the *default* schema — and alembic represents the default schema as `None`, which
  broke reflection, every foreign key comparison and the `alembic_version` exclusion. Do not
  remove the pin: without it autogenerate can never be empty.
- **Constraint names:** `Base.metadata` carries a naming convention (`pk_` / `fk_` / `uq_` / `ix_` /
  `ck_`), so Postgres never invents a name Alembic cannot reproduce. Do not name constraints by
  hand unless one genuinely needs to differ.
- **Errors:** services raise from `app/domain/errors.py`; `app/middleware/errors.py` is the only
  place that maps them to HTTP. The mapping is the public contract: `AnvexError` → 500,
  `ValidationError` → 422, `UnauthorizedError` → 401, `ForbiddenError` → 403, `NotFoundError` → 404,
  `ConflictError` → 409, `ExternalServiceError` → 502 (we are up, the upstream is not).
- **Error body:** *every* non-2xx response — domain error, pydantic 422, unknown-route 404, or an
  unhandled crash — uses one shape, `app.schemas.errors.ErrorResponse`:
  ```json
  {"error": {"code": "not_found", "message": "stock 'AAPL' was not found.",
             "details": {"resource": "stock", "identifier": "AAPL"}, "request_id": "8f1c…"}}
  ```
  All four keys are always present and `details` is `{}` rather than `null`, so a client indexes it
  unconditionally and branches on `code`, never on `message`. A 500 always returns the fixed message
  `"An unexpected error occurred."` with empty `details`; the traceback is logged, never returned.
  API tests assert this shape — changing it is a breaking API change.
- **Request ID:** every request carries `X-Request-ID` (the inbound one when it is a safe short
  token, otherwise a generated UUID4). It is bound into the structlog context, echoed on the
  response, and repeated as `error.request_id` so a reported failure maps to one log line.
- **App:** `app.main.create_app(settings)` is the factory; `app = create_app()` is what uvicorn
  imports. `/health` is liveness (no I/O) and `/health/ready` is readiness (`SELECT 1`, 503 on
  failure); both are unversioned. Versioned routers are included in `app/api/v1/__init__.py`.
- **Config:** `pydantic-settings` `Settings` object in `app/settings.py`, read once, injected via
  `deps`. Never `os.getenv` outside that module.
- **Logging:** structured, request-id-tagged. No bare `print`.
- **Auth:** JWT access + refresh (`python-jose`), bcrypt password hashing, `OAuth2PasswordBearer`.
  The pair is `app.schemas.auth.TokenPair` (`{access_token, refresh_token, token_type}` — the
  frontend parses those keys). **Every token carries a `type` claim** (`"access"` / `"refresh"`)
  and verifying a token means checking that claim as well as the signature, or an access token
  can be redeemed as a refresh token. A refresh token travels in a JSON body, never a query string.
- **The token `type` check is enforced by the API's shape, not by a reminder.**
  `app.domain.auth.decode_token` takes a keyword-only `expected_type` with **no default**, so a
  caller that forgets it gets a `TypeError` at the call site rather than a renewal hole in
  production; `decode_access_token` / `decode_refresh_token` pin it in their names. There is
  deliberately no "just verify this token" entry point. Token failures are `TokenError` subclasses
  of `UnauthorizedError` — `invalid_token` (malformed, tampered, wrong key or algorithm),
  `token_expired`, `wrong_token_type` — so a client refreshes on expiry and re-authenticates
  otherwise, while *why* a signature failed stays unsaid. **When a rule must not be forgotten,
  make the signature require it.**
- **`OAuth2PasswordBearer`'s `tokenUrl` must name the route that actually exists.** It is the
  only thing pointing Swagger's *Authorize* button at the login endpoint, nothing validates it,
  and a wrong value fails silently as a /docs page that cannot sign in. It lives as `TOKEN_URL`
  in `app/deps/auth.py` (`"v1/auth/login"` — relative, as OpenAPI wants) and a test asserts it
  equals the mounted path.
- **An endpoint keyed on an unauthenticated identifier answers identically whether or not that
  identifier exists.** Same status, same code, same message, same `details` — and the same work,
  so response *time* does not answer the question either (`AuthService.login` hashes against a
  decoy digest on the miss path). This covers login, password recovery, and any future
  "is this taken" probe reachable without a token. Both the old `/v1/login` and the old
  `/v1/recovery` failed this: recovery answered 404 with the username echoed back, which made it
  a free enumeration API.
- **Domain takes the clock as a parameter.** No module under `app/domain/` reads the clock, and
  every function that needs the time takes `now` as a required keyword-only, **timezone-aware**
  datetime (a naive one is a `ValueError`: `.timestamp()` would silently resolve it in the server's
  local zone). The service reads the real clock **once** per operation and passes it down, so a
  minted pair shares one `iat` and expiry is testable without a `sleep`. This applies to any
  time-dependent rule, not just auth — and it means a domain module must not delegate a time check
  back to a library either (`decode_token` disables `python-jose`'s own `exp` verification and
  compares against the injected `now` itself). **A purity convention that lives only in prose gets
  broken:** each domain module's unit tests parse its source and fail on a clock call or a
  `fastapi`/`app.settings` import, the way `tests/unit/test_domain_auth.py` does.
- **`app/utils/security.py` owns the bcrypt 72-byte boundary**, because it is the only layer that
  knows the encoding: the schema cap is 72 *characters* and bcrypt's limit is 72 *bytes*, so a
  25-character multibyte password passes validation and still overflows. `hash_password` **raises**
  (a write we control — never persist a credential silently equal to its own prefix);
  `verify_password` **returns `False`** and never raises, for an over-long candidate and for a
  stored hash that is empty, foreign or corrupted. A broken hash fails one login, not the process.
- **Lists return `Page[T]`, never a bare array.** `app.schemas.pagination.Page` is the one envelope:
  `{items, total, limit, offset, has_more}`, offset paging, `limit` bounded by `MAX_PAGE_LIMIT`.
  `total` counts every matching row, `has_more` is computed, and the two bounds are echoed so the
  response is self-describing. A bare array cannot gain a key later without breaking clients.
- **Schemas agree with columns by construction.** A validator's length cap is *imported* from the
  model module's constant, never retyped, so widening a `VARCHAR` cannot leave a stale number in a
  schema — and an oversized field is a 422 at the edge rather than a `StringDataRightTruncation`.
  An output field is `| None` **exactly** when its column is nullable; a defensive `| None` makes
  every client null-check a state that cannot happen. Money is `Decimal` end to end (it serialises
  as a quoted JSON string — a JSON number would be a float and lose the fourth decimal place).
- **A secret is never a field on an output schema.** `tests/unit/test_schemas.py` walks every
  pydantic model in the `app.schemas` package and fails on a password-ish field outside a small
  allowlist of request bodies, so a new schema is covered the moment it exists rather than when
  somebody remembers to check it.
- **Containers:** the compose service names *are* the in-network hostnames the settings default
  to — `db`, `db-test`, `redis`, `minio`, `api`, `worker`, `beat`, `web`. Renaming a service is a
  config change. Containers reach each other by service name on the service's own port; the
  published host ports are a developer convenience and live in `.env` (`*_HOST_PORT`).
- **`db` vs `db-test`:** `db` is the application database (named volume, survives restarts).
  `db-test` is the **only** database a test may write to — a second Postgres with no volume, so it
  starts empty every time. Nothing in `app/` ever knows it exists.
- **Container healthcheck is `/health`, never `/health/ready`.** Readiness depends on Postgres, so
  wiring it to the healthcheck turns a database blip into an API restart loop. `/health/ready` is
  what `depends_on` conditions and (later) an ALB target group poll.
- **Image layout:** the backend image installs its venv at `/opt/venv`, deliberately outside the
  `/app` working directory, because the dev compose service bind-mounts the source over `/app`.
  The project itself is never installed — the source is imported from the working directory, which
  is what makes the bind mount plus `--reload` work.

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
  - `tests/unit/` — **domain, utils, and service logic against in-memory fakes. Pure, fast, no
    fixtures, no I/O.** This is where the bulk of the tests live. Cover the edge cases here, not
    through the API. A service test belongs here when a fake repo answers the question and in
    `tests/integration/` when only real SQL can.
  - `tests/integration/` — repos and services against a real Postgres (compose `db-test`), each
    test in a rolled-back transaction. Clients are tested with `respx`-mocked HTTP; never hit a
    live vendor API in a test.
  - `tests/api/` — route contract tests: status codes, response shape, auth enforcement,
    validation errors. Services are stubbed via dependency overrides.
- Shared fixtures in `tests/conftest.py`; model factories in `tests/factories/`.
- **The default suite must run with Docker stopped.** A test that needs a container skips itself,
  never fails. Tests needing the *whole* compose stack are opt-in behind `ANVEX_COMPOSE_TEST=1`.
- Run: `uv run python -m pytest` (`uv run pytest` is blocked by an Application Control policy on
  the current dev machine). Full guide: [`backend/docs/testing.md`](backend/docs/testing.md).

#### The harness — fixture names and rules

**Extend `tests/conftest.py`. Never start a parallel conftest or a second set of database
fixtures.** Supporting modules beside it: `tests/database.py` (how the harness reaches and
migrates `db-test`), `tests/helpers.py` (shared assertions and stubs), `tests/factories/`.

| Fixture | Scope | Gives you |
| --- | --- | --- |
| `settings` | function | `Settings` with CORS origins and log level pinned. Override it in a module to pin one more field; do not construct a second `Settings`. |
| `app` | function | `create_app(settings)`. **Fresh per test**, so `dependency_overrides` cannot leak. |
| `client` | function | `AsyncClient` over `ASGITransport(raise_app_exceptions=False)` — the flag is load-bearing; without it a 500 re-raises into the test instead of returning the body a real client sees. |
| `mock_http` | function | a `respx` router intercepting every outbound `httpx` call |
| `database_available` | session | skips unless `db-test` answers |
| `db_engine` | session | `AsyncEngine` on the migrated `anvex_test` (`NullPool`) |
| `db_connection` | function | a connection holding an open, never-committed transaction |
| `db_session` | function | **the usual one** — an `AsyncSession` whose writes are rolled back |
| `db_app` / `db_client` | function | `app` / `client` with `deps.get_session` resolved to `db_session` |
| `throwaway_database_url` | function | a brand-new empty database, dropped afterwards |

- **What each tier may touch.** `tests/unit/` — nothing but fakes (no fixtures, no I/O).
  `tests/api/` —
  `client` plus `app.dependency_overrides`; never a database. `tests/integration/` — `db_session`
  for repos and services, `mock_http` for clients.
- **Isolation survives `commit()`.** `db_connection` never commits its transaction and `db_session`
  joins it with `join_transaction_mode="create_savepoint"`, so a service's `session.commit()`
  releases a savepoint and behaves normally while the outer transaction is still rolled back at
  teardown. Do not add per-test cleanup or truncation — there is nothing to clean up.
- **Skipping is fixture-driven, not directory-driven.** Requesting any `db_*` fixture auto-applies
  the `db` marker, and the test skips with a reason when Postgres is unreachable; `-m "not db"`
  deselects the tier. So do not ask for `db_session` "just in case", and a `respx`-only test in
  `tests/integration/` keeps running with Docker stopped.
- **The harness migrates, it does not `create_all`.** `db_engine` runs `alembic upgrade head` once
  per session against `db-test`, which starts empty every time (tmpfs, no volume). Alembic's
  `env.py` honours `config.attributes["sqlalchemy.url"]` so the harness can point it at a specific
  database without mutating the environment; nothing in `app/` uses that hook.
- **The test DSN lives in `tests/database.py`, not `app/settings.py`** — §4 says nothing in `app/`
  knows `db-test` exists. It is a test-only `BaseSettings` reading `POSTGRES_TEST_HOST`
  (default `localhost`), `POSTGRES_TEST_PORT` (falls back to `POSTGRES_TEST_HOST_PORT`) and
  `POSTGRES_TEST_DB` from the same repo-root `.env`.
- **Assert error bodies with `assert_error_envelope`** from `tests/helpers.py`, and keep a
  session-taking route off Postgres with `override_session(app, StubSession(...))` from the same
  module. `ERROR_BODY_KEYS` is defined there once.
- **An API test overrides the resource's `get_x_service`, and nothing else.** Point it at a real
  service built on an in-memory repo (`tests.helpers.FakeUserRepo`, `make_user`) rather than a
  hand-written stub of the service itself: the route, the middleware, the error envelope and the
  service's own branches are then all genuinely under test, with no database and no skip. Stub the
  service only when the test is about the handler's plumbing rather than the behaviour behind it.
  A module-local `app` fixture that installs the override (and, where the tier needs one, a probe
  route behind `CurrentUser`) is the idiom — extend `app`/`settings` by name, per the table above.
- **Factories:** one `Factory` subclass per model in `tests/factories/`, `@register`ed and
  re-exported from `tests/factories/__init__.py` so tests write `from tests.factories import
  UserFactory`. Unique columns come from `self.sequence()`, never from faker (seeding resets per
  test, so faker repeats within one test). A factory flushes; it never commits. A child or
  association factory **takes its parent from the caller**
  (`WatchlistDataFactory().create(db_session, watchlist=w, stock=s)`) rather than inventing one,
  so a test's object graph is exactly as large as the test says it is.

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
