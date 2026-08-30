# ADR-0002 — Async everywhere in the backend

## Status

**Accepted** — ANV-1, and load-bearing from ANV-3 onward. Recorded in ANV-39.

## Context

`AverageInvestorApi` was FastAPI with synchronous SQLAlchemy 1.4: `create_engine`,
`Session`, blocking `requests` for anything outbound. FastAPI runs a `def` handler in a
thread-pool worker, so that arrangement works and scales badly in exactly one shape — a
handler that waits on something. Every handler in this application waits on something: a
database round trip, and for news, a vendor.

The application also has to serve two callers of the same logic. A route handles a request
and a Celery task runs the same use case on a schedule. If the service layer is sync, the
route pays the thread pool; if it is async, the task needs a bridge. One of those costs had
to be chosen.

## Decision

`async def` end to end. Route handlers, dependencies, services, repos and clients are all
coroutines. SQLAlchemy 2.0 async ORM on `asyncpg`; `httpx.AsyncClient` for HTTP; `aioboto3`
for S3. No `Session`, no `create_engine`, no `requests` anywhere — and the client layer's
AST sweep fails on an import of `requests` or a call to `time.sleep`.

Celery is synchronous and stays synchronous. Exactly one module bridges the two —
`backend/app/jobs/base.py`'s `run_async` — and a task that invents its own bridge is how
this layer rots.

## Consequences

**Lazy loading is not available, and the API is shaped around that.** Touching an unloaded
relationship under asyncio raises `MissingGreenlet`, so every relationship a caller will
read is named in `.options(selectinload(...))` by the query that loads it, and where the
eager load is optional it is a **separate repo method** (`get_by_id` versus
`get_with_entries`) rather than a boolean flag. That turns out to be a benefit as often as a
cost: `WatchlistService._resolve_owned` deliberately fetches the parent *without* its
entries so that refusing a cross-account read does no work proportional to a collection the
caller may not see.

**Alembic had to become async, and one of its settings is a landmine.**
`backend/app/db/migrations/env.py` is async and reads the URL from `get_settings()`, never
from `alembic.ini`. Its connection pins `search_path` to `public`: the login role is also
called `anvex`, so Postgres' stock `"$user", public` search path made `anvex` the *default*
schema — and Alembic represents the default schema as `None`, which broke reflection, every
foreign-key comparison and the `alembic_version` exclusion. Remove the pin and autogenerate
can never be empty again.

**The Celery bridge has a stated, accepted cost.** `run_async` creates a loop per task, runs
the coroutine, and disposes the engine *inside that loop* before closing it — so a pooled
connection can never outlive the loop that opened it. The price is that a worker gets **no
cross-task connection pooling** and pays one Postgres connect per task. That is right while
a task is a batch (one session, many queries, the handshake amortises) and the alternative —
a long-lived loop in a background thread — adds a thread whose crash is invisible and a pool
that must survive child recycling. The trigger to revisit it is a measurement, not a
feeling: a task whose own runtime approaches the connect cost, or a queue of many short
tasks per second.

**Forking a prefork worker had to be handled explicitly.** `reset_engine()` is wired to
Celery's `worker_process_init`; it is deliberately not a coroutine, because a just-forked
child has no loop to await one in, and it deliberately does **not** close anything, because
those descriptors are still the parent's and closing them turns a latent bug into a certain
one. The general rule that fell out of this is worth more than the fix: **split an object by
loop-boundness and fork-safety, and scope the halves differently.** `get_settings()` and
`aioboto3.Session` are cache-like and loop-free, so they are process-wide singletons; an
`AsyncEngine` and an `S3Client` own connections, so they are per-request or per-task and are
never constructed at import or at worker boot.

**Async makes purity worth enforcing.** A domain function that read the clock or called
`uuid4()` would be untestable without freezing something; a domain function that did I/O
would be untestable without a loop full of fixtures. So `backend/app/domain/` takes `now`
and any uniqueness token as required keyword arguments, and each domain module's unit tests
parse its own source and fail on a clock call, a `uuid4`, or a `fastapi` import. The
convention lives in a test rather than in prose because a convention that lives only in
prose gets broken.

**The test harness had to be built for it.** `pytest-asyncio` in `asyncio_mode = "auto"`,
`httpx.AsyncClient` over `ASGITransport` for API tests, and a rollback fixture that joins
the session to an outer transaction with `join_transaction_mode="create_savepoint"` so a
service's real `commit()` behaves normally and is still discarded at teardown.
