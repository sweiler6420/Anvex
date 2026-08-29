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
| ANV-15 | Watchlists — reorder domain, service and routes | Next |
| ANV-16 … ANV-41 | see `backlog.md` | Not started |

**993 tests** passing with `db-test` up (780 with it stopped, DB tier skipping), 91% coverage.

---

## Active carry-overs

Only what is still outstanding. Once a ticket consumes one of these, delete it — the full record
stays in [`ticket-log.md`](./ticket-log.md).

**For ANV-15 (watchlists) — the two that matter most:**
- **Ownership is the service's job.** The repo layer deliberately provides no "owned by user" query.
  Compare `watchlist.user_id` to the token subject in the service, or one user gets another's list.
- **Refuse with 404, not 403** — identical to "does not exist", raised *before* any query, exactly as
  ANV-12 did for users. A 403 confirms which watchlist ids are real.
- `max_position` returns `None` on an empty watchlist, not `-1`; the append rule
  `(max_position or -1) + 1` is a rule and belongs in `app/domain/watchlist.py`.
- `position` is deliberately **not** unique per watchlist, so `set_positions` can apply a mid-swap
  state in one flush.
- **Parent-resolution shape from ANV-14 transfers directly:** resolve the watchlist first, then page
  its entries — unknown parent 404, known parent with no rows is an empty 200. The difference is
  that a watchlist *is* owned, so the 404-not-403 rule stacks on top; a stock has no owner and its
  404 is the plain kind.
- Reuse `resolve_window` from `app/domain/stock_data.py` (it is generic) rather than re-deriving
  offset handling, and follow `resolve_candle_query`'s division of labour: one small pure `resolve_*`
  called once at the top of the service method, returning a frozen value the rest reads. **Validate
  input before checking existence**, so a malformed request never reveals whether an id is real.
- API tests need **two accounts in `FakeUserRepo`** to prove ownership isolation with no database.

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

