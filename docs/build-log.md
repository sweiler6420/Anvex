# Anvex build log

Status and live context for the monorepo build. **Read this first after any session restart** — it
is the handoff document, and it is kept short on purpose.

| Read | For |
| --- | --- |
| this file | current status, environment traps, and the carry-overs still outstanding |
| [`../CLAUDE.md`](../CLAUDE.md) | the architecture contract — what goes where, and why |
| [`backlog.md`](./backlog.md) | the ticket specs, in execution order |
| [`ticket-log.md`](./ticket-log.md) | the archive: what each completed ticket decided and found |

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
| ANV-42 | Drop passlib, hash with bcrypt directly | **Done** *(inserted — Stephen's call)* |
| ANV-12 | Users service and routes | **Done** — *E3 Auth complete* |
| ANV-13 | Stocks service and routes | **Done** |
| ANV-14 | Stock data service and routes | **Done** |
| ANV-15 | Watchlists — reorder domain, service and routes | **Done** |
| ANV-16 | Politicians seed data, service and routes | **Done** — *E4 Core features complete* |
| ANV-17 | Client base | **Done** |
| ANV-18 | AlphaVantage client | **Done** |
| ANV-19 | NewsAPI client, service and routes | **Done** |
| ANV-20 | S3 client and storage service | Next |
| ANV-21 … ANV-41 | see `backlog.md` | Not started |

**2,261 tests** passing with `db-test` up (1,996 with it stopped, DB tier skipping), 99% coverage.
`ruff check` and `ruff format --check` are both clean across all 152 files.

---

## Active carry-overs

Only what is still outstanding. Once a ticket consumes one of these, delete it — the full record
stays in [`ticket-log.md`](./ticket-log.md).

**For ANV-20 (S3):**
- **`BaseHTTPClient` is HTTP transport and you are not on it.** An SDK-reached vendor shares the
  *error and logging* contract but not the transport. `ExternalServiceError` with a `reason` in
  `details` is the contract; the `Failure` enum in `app/clients/base.py` is HTTP-shaped, so decide
  deliberately whether to reuse its members or name S3's own.
- **The AST sweep globs the package**, so it already covers your module — including the
  `app.schemas` ban. Define your return models in `app/clients/s3.py`.
- **Reuse the not-configured pre-flight pattern, do not re-derive it:** check the credential
  *before* the call, raise `ExternalServiceError` **directly** (not via `_error`, which needs a
  `Failure` describing how a call went wrong — no call was made), with
  `details = {"reason": "not_configured", "setting": "<ENV_VAR>"}`. S3 has real local defaults
  (MinIO), so it may not need this — but this is the shape if it can be unconfigured.
- **The client-lifetime question is open and yours if you want it.** `app/deps/news.py` builds and
  closes a client per request, giving up cross-request pooling. `aioboto3` has the same shape of
  question and would be the second caller — that is when a lifespan-owned, worker-shareable client
  becomes knowable. Only the dep factory has to change.
- `tests/helpers.FakeNewsApiClient` + `make_article` are the precedent for faking a client: record
  the calls, raise real `ExternalServiceError`s, **never return `None` on failure**.

**For ANV-22 (ingest) — what ANV-18 deliberately left you:**
- You receive `IntradaySeries` carrying `timezone` (e.g. `"US/Eastern"`) and a tuple of
  `IntradayCandle` in the vendor's order (**newest first**).
- **All of this is yours, none of it is done:** the 08:05–17:00 filter (use `series.timezone`, do
  **not** hardcode a zone); quantising `Decimal` prices to the model's scale — the client hands you
  full vendor precision on purpose; mapping `open/high/low/close` → `open_price/…` and attaching
  `stock_id`; which months to fetch; and the 5-calls-per-minute pacing across the fan-out. **The
  client will never sleep for you** — a test asserts `sleeps == []` across two calls.
- An empty series is a legitimate `()`, not an error — decide what "nothing traded" means.
- `ExternalServiceError` with `details["reason"] == "rate_limited"` is your reschedule signal, and
  it carries **no `attempts` key** when it came from a 200 body.

**For ANV-18 (AlphaVantage) — and every client after it:**
- Subclass `BaseHTTPClient`: set `vendor` and `base_url`, keep the key as a **`SecretStr` on the
  instance**, and return it from `auth_params()`. **Do not call `.get_secret_value()` yourself** —
  the base unwraps it while building one request and never stores the plaintext.
- A vendor method is one line: `payload = await self.get_json(path, params=...)` then
  `Model.model_validate(payload)`. **No `try`, no status check, no retry loop, no logging** —
  the base owns all of it.
- **The AST sweep will fail you** for importing `app.schemas`, `app.models`, `app.repos`,
  `app.db`, `app.services`, `app.jobs`, `app.api`, `sqlalchemy`, `requests`, or any `app.` import
  outside `{app.clients, app.clients.base, app.domain.errors, app.settings}`. `app.schemas` is
  forbidden **on purpose** — it is Anvex's public shape and a vendor does not share it, so define
  the vendor's model in the client module.
- **AlphaVantage's rate-limit response is a 200 with a JSON "Note"/"Information" body**, not a 429,
  so the base cannot see it. Detect it in the parser and raise
  `ExternalServiceError(..., details={"reason": "rate_limited"})`. If ANV-19 needs the same, add a
  `_check_payload` hook to the base rather than duplicating.
- Proactive quota throttling (5 calls/min) is **not** a client concern — that belongs to the job
  that fans out (ANV-22).
- Ticker normalisation is the service's job; take the symbol as a primitive.
- Tests: `respx` via the `mock_http` fixture, never a live vendor. Use the
  `sleeps` / `jitter=lambda: 0.0` fixture idiom from `test_client_base.py` to assert on retries
  without real waiting.

**For any ticket writing a service — the sweep pattern (new, from ANV-15):**
Any property that must hold for *every* use case (auth required, resource noun stable, no commit on
a read) is better expressed as one parameterised sweep whose case list is **derived** from
`vars(XService)` and asserted complete, than as N hand-written tests one of which will be forgotten.
ANV-15's ownership sweep fails the suite if a new use case is added without isolation coverage.

**For ANV-22 (ingest):**
- `bulk_upsert` is a Core statement — it does **not** update the session's identity map. Re-read or
  `expunge_all` if ORM objects are still held.
- **No repo-level dedupe.** A batch containing an internal duplicate raises `cannot affect row a
  second time`; deduplicate in `app/domain/ingest.py` first.

**For any ticket writing a service:**
- Pre-check for the message, constraint for the correctness: keep `*_exists` so the 409 can name
  `details.field`, *and* catch `IntegrityError` — with `await session.rollback()` **first**, because
  Postgres aborts the transaction and refuses everything after it.
- Translate `app/utils/` exceptions at the service; utils raise builtins by layering rule, and
  uncaught they become 500s for input the API should have refused.
- Add fakes **beside** the existing ones in `tests/helpers.py`, and keep them faithful to the
  awkward parts of the real repo — a forgiving fake silently passes the bug the test exists to catch.

**Still unimplemented, deliberately:**
- **No mail client.** `POST /v1/auth/recovery` logs `delivered=False` and returns 202, behind a
  `TODO(ANV-mail)` that a unit test asserts is still present. Wiring real delivery needs its own
  ticket.

