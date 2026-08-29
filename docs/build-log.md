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
| ANV-20 | S3 client and storage service | **Done** |
| ANV-21 | Celery application and worker wiring | **Done** |
| ANV-22 | Stock ingest job | **Done** — *E5 complete; **the backend is done*** |
| ANV-23 | Vite scaffold, Tailwind and test harness | Next — *first frontend ticket* |
| ANV-24 … ANV-41 | see `backlog.md` | Not started |

**2,811 tests** passing with the full stack up (2,497 with Docker stopped — DB, S3 and broker tiers
skip), 99% coverage. `ruff check` and `ruff format --check` clean across 176 files.
Container tiers genuinely execute: **276 DB**, **29 S3**, **9 broker**.

`AverageInvestorService` is now fully replaced and can be deleted. Two things it did that Anvex
does not yet: **deep historical backfill** (its 43-month sweep — `ingest_month` accepts any explicit
month, but nothing infers a gap older than the watermark, so closing one means dispatching months by
hand) and the **EC2/Lambda deployment glue** (deliberately gone, not ported — beat + a worker
container is the equivalent). Both are ticket-sized additions, neither needs the old code.

---

## Active carry-overs

Only what is still outstanding. Once a ticket consumes one of these, delete it — the full record
stays in [`ticket-log.md`](./ticket-log.md).

**For any ticket writing a service — the sweep pattern (new, from ANV-15):**
Any property that must hold for *every* use case (auth required, resource noun stable, no commit on
a read) is better expressed as one parameterised sweep whose case list is **derived** from
`vars(XService)` and asserted complete, than as N hand-written tests one of which will be forgotten.
ANV-15's ownership sweep fails the suite if a new use case is added without isolation coverage.

**For any ticket writing a service:**
- Pre-check for the message, constraint for the correctness: keep `*_exists` so the 409 can name
  `details.field`, *and* catch `IntegrityError` — with `await session.rollback()` **first**, because
  Postgres aborts the transaction and refuses everything after it.
- Translate `app/utils/` exceptions at the service; utils raise builtins by layering rule, and
  uncaught they become 500s for input the API should have refused.
- Add fakes **beside** the existing ones in `tests/helpers.py`, and keep them faithful to the
  awkward parts of the real repo — a forgiving fake silently passes the bug the test exists to catch.

**For the frontend epic (ANV-23 onward) — API contract facts the UI must respect:**
- **Node is not installed on this machine, by choice.** Every `npm` / `vite` / `vitest` command runs
  **inside Docker**. `node:22-alpine` is already pulled locally.
- **Prices are quoted JSON strings**, not numbers — `"1234.5678"`. That is what preserves the fourth
  decimal; JSON numbers go through float and lose it. Chart code must `Number()` them.
- **`stock_data`'s `datetime` is naive on purpose** — no `Z`, no offset. It is the exchange's local
  clock, and stamping UTC on 09:30 ET would move every candle. Do not "fix" it.
- **The error envelope is fixed and every non-2xx uses it:**
  `{"error": {"code", "message", "details", "request_id"}}`, `details` always `{}` rather than
  `null`. **Branch on `code`, never on `message`.**
- **Auth codes to handle:** `token_expired` → refresh; `invalid_token` / `wrong_token_type` → log
  out. Refresh must be **single-flight** — the old app fired one refresh per concurrent 401 — and it
  triggers on **401**, not 403 (the old app had that backwards).
- **`POST /v1/auth/refresh` takes a JSON body**, not a query parameter.
- Routes are `/v1/auth/{login,refresh,recovery}`, `/v1/users` + `/me` + `/{id}`, `/v1/stocks` +
  `/{id}` + `/by-ticker/{ticker}`, `/v1/stocks/{id}/data`, `/v1/watchlists` (+ `/stocks` sub-routes),
  `/v1/politicians`, `/v1/news/{top,by-symbol/{ticker}}`.
- **Lists return `Page[T]` = `{items, total, limit, offset, has_more}`.**
- **Never persist a password.** The old app wrote it to `localStorage` for "remember me" — username
  only, and the access token stays in memory.

**Still unimplemented, deliberately:**
- **No mail client.** `POST /v1/auth/recovery` logs `delivered=False` and returns 202, behind a
  `TODO(ANV-mail)` that a unit test asserts is still present. Wiring real delivery needs its own
  ticket.
- **No deep historical backfill.** `ingest_month` takes any explicit month, but nothing infers a gap
  older than the watermark.
- **`download_url` exists but no route mounts it** — whether a presigned URL should leave the API is
  an open decision, not an oversight.

