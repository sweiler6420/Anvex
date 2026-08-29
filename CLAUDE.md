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
- **`base.py` is the shape of the layer, and a subclass is small on purpose** (ANV-17). A client
  sets two class attributes (`vendor`, `base_url`), overrides `auth_params()` / `auth_headers()`
  if the vendor wants a credential, and writes one `async` method per vendor operation that calls
  `self.get_json(...)` and returns a typed model. It contains no `try`, no status-code check, no
  retry loop and no logging — each of those is a decision three vendors would otherwise make three
  subtly different ways. **If a vendor module has a `while` in it, the base is missing a feature.**
  `BaseHTTPClient` is the *HTTP* foundation; a vendor reached through an SDK rather than a URL
  (ANV-20's `aioboto3` S3 client) shares the error and logging contract below but not the
  transport.
- **`ExternalServiceError` is the layer's one exit.** Unreachable host, timeout, 5xx, 4xx, rate
  limit, a body that is not JSON — all of them leave `app/clients/` as that exception (→ 502) and
  nothing else does. A raw `httpx` exception escaping would make every service import `httpx`; a
  `JSONDecodeError` escaping would be a 500 for something the client already understood. `details`
  carries `service`, `reason`, `attempts` and — where they exist — `status_code` and `retry_after`,
  and **never** the vendor's body or URL: §4 makes the error body a public contract, and forwarding
  upstream output through it turns an internal detail into an API. This is deliberately *unlike*
  `app/data/`, which raises a plain `ValueError`, because a client failure is always inside a
  request or a job and already has a status code waiting for it.
- **4xx is never retried; 5xx and transport errors (timeouts included) are.** Retrying a 401 turns
  one permanent failure into three. **429 is its own case** — a "not now" rather than a "never" —
  so it gets a separate, shorter attempt budget, and a `Retry-After` is honoured *but capped*: a
  vendor asking for 60 seconds is asking for longer than a request path may be held open, so the
  call fails immediately with `retry_after` in `details` and the caller reschedules. Every wait is
  `await`ed (`asyncio.sleep`) and jittered downward, and the loop is bounded twice — by attempt
  count *and* by a wall-clock budget, because attempts alone do not stop three slow-but-not-dead
  responses adding up. A blocking `time.sleep` anywhere in the package fails the layering test.
  A malformed 200 body is *not* retried: a vendor answering with HTML is broken, not blipping.
- **Timeouts are four named numbers, never one.** A bare `timeout=5` sets connect, read, write and
  pool at once and hides which one was meant. Connect is short (a handshake is fast or the host is
  gone); read is generous (a vendor is allowed to think about a query).
- **A credential is unwrapped in the request builder and nowhere else, and no URL is ever logged
  raw.** The subclass holds the `SecretStr` and returns it from `auth_params()`/`auth_headers()`;
  the base unwraps it while building one request and lets the plaintext go out of scope with the
  call. Logging goes through `redact_url`, which blanks any query value whose *name* looks like a
  credential **and** any value that *is* one of the call's secrets — two independent tests, because
  a vendor that names its key `u` defeats the first and a key we have not enumerated defeats the
  second. Bodies and headers are never logged at all. Redaction is by construction, not by
  remembering: a subclass cannot log a raw URL because it never gets one.
- **Redirects are not followed.** Following one would resend the credential-bearing URL to whatever
  host the vendor named, so a 3xx is a failure here, not a hop.
- **One `httpx.AsyncClient` per client instance, created lazily and closed explicitly.** Pooling
  connections, DNS and TLS is why the base owns the client instead of each call opening one.
  Closing is final — a request after `aclose()` raises, mirroring httpx's own refusal to reopen,
  because silently reconnecting hides a lifecycle bug in something meant to be long-lived.
- **The layering is enforced by an AST sweep, not by this paragraph.**
  `tests/unit/test_clients_base.py::TestTheLayerStaysInItsLane` parses every module in the package
  and fails on an import of `sqlalchemy`, `requests`, `app.repos`, `app.db`, `app.models`,
  `app.services`, `app.schemas`, `app.api` or `app.jobs`; on any `app.` import outside the
  allow-list (`app.clients.*`, `app.domain.errors`, `app.settings`); on a `time.sleep` call; and on
  a bare `print`. **`app/schemas/` is forbidden on purpose** — it is the API's public shape and a
  vendor does not share it, so a vendor payload is parsed into a model *defined in the client
  module*. A client wanting a normalised ticker is asking the wrong layer; §4 makes that the
  service's job.
- **A vendor reached through an SDK keeps the contract and drops the transport.** `BaseHTTPClient`
  is *HTTP*: it builds an `httpx.Request`, retries it, redacts its URL and decodes its body. An SDK
  (ANV-20's `aioboto3`) does all of that itself and offers no seam to hand it a client, so
  subclassing to inherit a lifecycle and then override everything that uses it would be a base class
  in name only. What such a module still owes is the four things above it depends on: one vendor and
  no Anvex knowledge, a typed model out, `ExternalServiceError` as its **only** exit, and structured
  logging with nothing secret written down. It reproduces the lifecycle by hand — lazily created,
  explicitly closed, a call after closing raises — and it imports `scrub` from the base rather than
  re-deriving it.
- **A `Failure` enum belongs to a transport, and its *values* belong to the layer.** `Failure` is
  HTTP-shaped, so forcing S3 onto it collapses distinctions a caller acts on: a missing key, a
  missing bucket and a rejected signature are 404/404/403 and would all read as `client_error`.
  A module whose vendor has failures the HTTP taxonomy cannot express declares its own `StrEnum`
  (`S3Failure`) — but **spells the overlapping members identically** (`transport_error`,
  `server_error`, `rate_limited`, `client_error`), so `details["reason"]` stays one vocabulary
  across `app/clients/` and only the genuinely new members have to be learned. The test that keeps
  this honest asserts the shared spellings *and* enumerates the additions.
- **Where the SDK owns the retry loop, `details` carries no `attempts`.** `botocore` already knows
  which S3 codes are transient and applies AWS's own backoff, so a second loop on top would retry at
  two layers; the client configures the SDK's instead. It does not report an attempt count, so the
  key is **absent** — ANV-18's rule, applied to a different cause: inventing a `1` would be worse.
- **Proactive quota throttling is not a client concern.** Honouring a `Retry-After` is; sleeping to
  stay under five-calls-a-minute is a scheduling decision for the job that fans out, because a
  request path cannot block to honour it.
- **A 2xx that means a failure is the vendor module's job, and it raises through the base.** Some
  vendors signal a refusal in the body rather than the status line — AlphaVantage answers a
  throttled request with `200` and a JSON `"Note"`/`"Information"`, and an unknown symbol with an
  `"Error Message"`. `BaseHTTPClient` cannot see any of that: a 2xx carrying valid JSON *is* a good
  response to it. So the subclass's **parser** detects it and raises `self._error(Failure.X)` —
  reusing the base's constructor rather than writing a message template, so a rate limit is
  indistinguishable to a consumer whether it arrived as a 429 or as a 200. `attempts` is omitted
  there, because the retry loop had already succeeded and there is no attempt count belonging to
  that failure; a fabricated `1` would be worse than an absent key. **ANV-19 was the second
  caller and still declined the base hook, after looking** — NewsAPI signals a refusal with
  `{"status": "error", "code": …}`, at a 200 as often as at a 4xx. The genuinely shared part had
  *already* been lifted: `_error(attempts=None)` owns the message templates, the `details` keys and
  the 502 contract, which is why a consumer cannot tell a body-detected rate limit from a
  transport-detected one. What remained differed in **kind**, not just in predicate — AlphaVantage
  tests for the *presence of a top-level key*, NewsAPI for the *value of a required field* plus a
  `code` → `Failure` lookup AlphaVantage has no analogue for — and a `payload -> Failure | None`
  hook expresses both only by being empty enough to express anything. Worse, it would have to
  answer a question neither vendor wants answered the same way: a check inside `request_json`
  implies a body-detected failure re-enters the retry loop, silently overturning ANV-18's asserted
  "the call is not repeated"; a check after the loop is a second traversal of a payload the parser
  is about to walk anyway, to save one `raise`. **So each check is written where it is read**, in
  the parser beside the knowledge of what the payload means. Generalise on a *third* vendor whose
  body-level failure is shaped like one of these two — not before.
- **A missing credential is refused before the request, and says which setting is missing.** A
  vendor key defaults to a blank `SecretStr`, so "not configured" is the state of every fresh
  clone rather than an edge case. The client checks first and raises `ExternalServiceError` with
  `details = {"reason": "not_configured", "setting": "<ENV_VAR>"}` — deliberately *not* through
  `_error`, whose `Failure` members all describe how a *call* went wrong, and no call was made.
  Still a 502, which is the honest status (Anvex is up; the upstream is unusable from here), and
  still `app/clients/`'s one exit. The point is the response: a keyless call would spend a round
  trip to be told `apiKeyMissing` and reach the operator as `reason: "client_error"`, which is
  indistinguishable from a malformed query. Naming our own env var in `details` is safe — it is a
  key name already committed to `.env.example`, not a value — and it is what makes a deployment
  mistake diagnosable without reading a log.
- **Where a vendor accepts its key either as a query parameter or as a header, send the header.**
  The base logs a redacted URL and logs no headers at all, so a key in the query is protected by
  redaction while a key in a header is never written down. Redaction is good; "not present" is
  better, and a URL escapes in ways a header does not (a proxy's access log, a `Referer`, a crash
  report quoting the request line). AlphaVantage has no choice; NewsAPI does, and takes the better
  one via `auth_headers()`.
- **A client does not round, quantise, or reshape a number to fit a column.** It parses the
  vendor's *string* straight into `Decimal` (never via `float`, which has already lost the value by
  the time `Decimal` sees it) and reports what was said. The storage scale lives in `app/models/`,
  which `app/clients/` may not import — that is the AST sweep telling you whose rule it is.
  Quantising, windowing and filtering are Anvex rules and belong in `app/domain/`. Equally, a
  parser never *silently* repairs: an unusable number is `ExternalServiceError`, never a `NaN` that
  reaches a `NUMERIC` column (the old ETL's `pd.to_numeric(errors="coerce")` is the bug being
  designed out).
- **Rule of thumb: if it makes a network call to something we do not own, it is a client.**

### `app/data/` — static and seed data
Checked-in reference data (JSON/CSV) and the loaders that read it: politician rosters, exchange
lists, ticker seeds. No network calls, no DB writes — loaders return parsed structures for a
service or a job to persist.

- **A data file is an envelope, not a bare array**: a JSON object with a required
  `provenance` string and a `rows` list. `app/data/loader.py` refuses a file whose
  `provenance` is missing or blank, which is the point — reference data is the one thing in
  the repo somebody will later mistake for *sourced* data, and the only reliable defence is
  making an unattributed file impossible to load. A synthetic fixture says so there (and a
  test asserts it still does); a licensed dataset names its licence there. Extra keys
  (`generated`, a version) are ignored, so a file can carry metadata without a schema change.
- **Rows are validated against the resource's `XCreate` schema on the way out**, so a loader
  returns `list[Model]` and a malformed file fails at *load* — naming the file, the row index
  and the field — rather than as an `IntegrityError` halfway through a bulk insert with half
  the batch already written. The first bad row stops the load; a partially valid dataset is
  not a dataset.
- **Failures are `SeedDataError`, a plain `ValueError`, and are deliberately not translated.**
  This layer has no Anvex error vocabulary and does not import `app/domain/errors.py`: a broken
  checked-in file is a repository defect, not a request, and the seed path is reached from a
  script or a job — never a route — so there is no status code for it to become. The script
  exits non-zero. (Contrast `app/utils/`, whose builtins *are* translated by the calling
  service, because those describe input a user supplied.)
- **Persisting is the service's job, and dedupe happens before the write.** A loader parses;
  a service validates ordering/uniqueness rules in `app/domain/`, upserts and commits. A seed
  is idempotent twice over and the halves are different mechanisms: `INSERT … ON CONFLICT DO
  UPDATE` on a real key makes a *second run* safe, and deduplicating the batch in `app/domain/`
  makes a *single run* safe (Postgres rejects a statement whose conflict target is hit twice).
  Neither substitutes for the other. `backend/scripts/seed_<resource>.py` is then a thin entry
  point — resolve, call one service method, report — with distinct exit codes for "the file is
  unusable" and "the database refused".

### `app/db/` — connection plumbing
Async engine, `async_sessionmaker`, declarative `Base`, session lifecycle, and the Alembic
`migrations/` tree. Nothing here knows what a Stock is.

### `app/deps/` — FastAPI dependencies
Reusable `Depends` providers: `get_session`, `get_current_user`, pagination params, rate-limit
guards, client/service factories. Dependencies wire objects together; they do not implement logic.

- **A client is not a repo, and its factory says so.** A repo is a stateless singleton and arrives
  as a keyword default on the service; a client owns an `httpx.AsyncClient` and therefore a
  lifetime, so it is a **required** keyword argument on the service and comes from a `yield`
  dependency that `aclose()`s it in the `finally`. Per-request construction gives up the
  cross-request pooling the client base exists for — one extra handshake — and buys no leaked
  pool, no shared mutable state between tests, and no lifespan edit for one endpoint. Constructing
  one is cheap and opens no socket (the base creates its transport lazily), so a request that
  never reaches the vendor costs nothing.
- **Share the part that has no event loop; build the part that has one per request.** ANV-20 was
  the second caller and settled the question ANV-19 left open. An application-scoped, lifespan-owned
  client is **not** the answer: an SDK client owns a connector bound to the loop that made it, so a
  Celery prefork worker — the thing the sharing was meant to serve — inherits either a dead loop or
  a forked socket two processes then read, and one shared pool makes one reset poison every request
  until the process restarts. What *is* shareable is the expensive, loop-free factory beside it:
  `aioboto3.Session` holds botocore's service-model cache, no socket and no loop, so it is an
  `lru_cache`d process-wide singleton (`app.clients.s3.default_session`) while the client stays
  per-request. **Split a client into "cache-like, loop-free" and "connection-owning, loop-bound"
  and scope the halves differently** — that generalises; "hoist the client into the lifespan" does
  not. A job that makes many calls buys the handshake back by holding **one** client for the whole
  task (`async with S3Client(...)`), never by constructing one at import or at worker boot.

### `app/domain/` — Anvex business logic
**Pure functions and pure classes.** This is where Anvex's actual rules live: watchlist reordering,
position math, indicator calculation, token claim construction, ingest windowing rules.

- Takes plain data in, returns plain data out. **No I/O of any kind** — no DB, no HTTP, no clock
  reads (pass `now` in), no env reads.
- Because it is pure, it is the cheapest and most valuable thing to unit-test. Test it exhaustively.
- **A rule about a *vendor's* data takes a `Protocol`, never the vendor's model.** `app/domain/`
  sits below `app/clients/` in the dependency order, so importing `app.clients.newsapi` to type a
  ranking rule would invert it. Declare the shape the rule actually reads as a `Protocol` in the
  domain module and make the function generic over it (`def f[T: Article](items: Sequence[T]) ->
  tuple[T, ...]`), so the service hands in vendor models, gets vendor models back, and has nothing
  to translate. The alternative — a domain dataclass the service maps onto and off again — buys a
  third spelling of one record, beside the vendor model and the `XOut` schema. The test for such a
  module is written against a three-line stub: if it can only be written by importing
  `app.clients`, the rule is in the wrong layer.
- **A rule that has to fit a *column* imports the column's constant from `app/models/`.** That is
  the one `app.` import this layer takes beyond `app.domain.*` / `app.schemas.*`, and it is §4's
  "never retype a column's cap" rule applied to a rule rather than to a validator: ANV-22's
  `quantise_price` reaches for `PRICE_PRECISION`/`PRICE_SCALE` so widening `NUMERIC(12,4)` cannot
  leave a stale `4` behind. It is also exactly the import `app/clients/`'s AST sweep forbids —
  which is *why* quantising is a domain rule and not a parser's (ANV-18 → ANV-22). Round
  `ROUND_HALF_UP`, not Python's banker's default, so the value written equals the value Postgres
  would have written itself and a `SELECT` after the `INSERT` agrees with the job that wrote it.
  A value the column cannot hold is a `ValueError` here, not a `DataError` halfway through a
  statement that names a column but not a row.
- **A rule about a time of day names the zone it is expressed in, and converts into it.** A
  wall-clock time carries no zone, so comparing one against a window means nothing until somebody
  says whose clock. Declare the window in a named zone constant, take the *quoted* zone as an
  argument, and convert — never `datetime.combine` without a `tzinfo` (it resolves in whatever zone
  the machine sits in) and never a hardcoded zone (that is a vendor metadata field being thrown
  away). An unrecognised zone name is a `ValueError`, not a fallback: guessing puts every row of the
  run in the wrong bucket with nothing in the data to say so afterwards. `zoneinfo` is a lookup
  table rather than an ambient input, so it does not offend the purity rule — the answer is the same
  on every machine, which is the property that rule exists to protect.
- **Rule of thumb: if a rule would still be true on paper without a computer, it belongs in domain.**

### `app/jobs/` — Celery tasks
The Celery app, task definitions, and beat schedule. A task is a thin entrypoint that resolves its
dependencies and calls **one** service — the same shape as an API handler. Business logic never
lives in a task body. Tasks are idempotent and safe to retry.

- **`run_async` is the one bridge, and every task crosses it unchanged** (ANV-21). Celery runs a
  task synchronously; everything else in the backend is `async`. Exactly one module reconciles
  that — `app/jobs/base.py` — and a task that invents its own bridge is how this layer rots. The
  shape is fixed and is two functions:
  ```python
  @celery_app.task(name="jobs.news.sync_symbol", bind=True)
  def sync_symbol(self: AnvexTask, symbol: str) -> int:
      return run_async(lambda: _sync_symbol(symbol))

  async def _sync_symbol(symbol: str) -> int:
      settings = get_settings()
      async with get_session() as session:                       # app/db/session.py
          return await NewsService(session, settings).sync_for_symbol(symbol=symbol)
  ```
  The sync half is the Celery entry point and contains nothing but the bridge call; the async half
  resolves its collaborators and calls **one** service — the API handler's rule, for the API
  handler's reason, so the same service serves a route and a job. `run_async` takes a **factory,
  not a coroutine**: `run_async(work())` would build the coroutine outside the loop that is about
  to exist, so passing one is a `TypeError` with the fix in the message. Exceptions are never
  caught there — a swallowed failure is a green job that did nothing, which is worse than a red one.
- **A pooled connection must not outlive the loop that opened it, and `asyncio.run` per task is
  how that is guaranteed.** `run_async` creates a loop, runs the coroutine, and calls
  `dispose_engine()` **inside that loop** before it closes. The accepted cost is stated rather than
  discovered: a worker gets **no cross-task connection pooling** and pays one Postgres connect per
  task. That is the right trade because a task is a batch (one session, many queries — the
  handshake amortises) and because the alternative, a long-lived loop in a background thread, adds
  a thread whose crash is invisible and a pool that must survive child recycling. Revisit it when a
  task's own runtime approaches the connect cost, or when the queue is many short tasks per second —
  not before, and with a measurement.
- **Split by loop-boundness, then by fork-safety; the halves are scoped differently.** ANV-20's
  client rule generalises past clients, and the database engine is the case that proves it. The
  engine *object* is inert, but its pool holds sockets, so: `aioboto3.Session` and `get_settings()`
  are cache-like and loop-free (`lru_cache`d, shared, fork-safe); an `S3Client` and an `AsyncEngine`
  own connections and are per-task. **Never construct a connection-owning object at import, at
  worker boot, or in a `worker_init` hook** — a prefork worker forks after that, and two processes
  on one descriptor corrupt silently rather than failing loudly.
- **A fork is handled in the child, synchronously, without closing anything.** `app/db/engine.py`
  exposes `reset_engine()` — wired to Celery's `worker_process_init` — which swaps in an empty pool
  via `sync_engine.dispose(close=False)` and forgets the engine. Two things are load-bearing: it is
  **not** a coroutine, because a just-forked child has no loop to await one in; and it does **not**
  close, because those descriptors are still the parent's and closing them turns a latent bug into a
  certain one. `current_engine()` is its read-only twin, and it exists so "has this process opened a
  pool?" can be asked without `get_engine()` answering it in the affirmative.
- **Every task passes an explicit `name="jobs.<module>.<function>"`.** Celery's default is the
  dotted import path, so renaming a module orphans every message already queued and every beat entry
  naming it. A sweep asserts the convention across the registry, and a second sweep derives the
  worker's import list from the contents of `app/jobs/` — a new job module that is not registered
  fails the suite instead of silently never running.
- **The base task sets the retry *spacing*; the task decides what is *retryable*.** `AnvexTask` has
  deliberately **no `autoretry_for`**: `app/clients/`'s one exit covers both "the vendor is down"
  (retry) and "the key is blank" (retrying forever will not fill it in), so a job branches on
  `details["reason"]` and calls `self.retry(exc=exc, countdown=self.retry_countdown())` when it
  means it. `retry_countdown()` exists because Celery's `retry_backoff` setting is only honoured by
  the wrapper `autoretry_for` installs — a manual `self.retry()` ignores it — and shipping a class
  attribute that silently does nothing is worse than a method.
- **At-least-once, and the asymmetry is the decision.** `task_acks_late=True` (a lost connection or
  a graceful restart redelivers — which is the same decision as "tasks are idempotent", not a second
  one) with `task_reject_on_worker_lost=False` stated explicitly. A lost *connection* is the
  network's fault and redelivery is free; a lost *process* is very often the *message*'s fault — the
  task that OOMed will OOM again, and redelivering it kills every worker that picks it up, with no
  natural end. **Lose the run, keep the workers**; beat re-drives the job on its next tick. A job
  that cannot tolerate that carries its own durable "did this window complete" record; it does not
  flip the flag. `worker_prefetch_multiplier=1` follows from `acks_late` — a bigger prefetch means a
  restart redelivers a batch of half-run tasks.
- **The broker's `visibility_timeout` must exceed `task_time_limit`.** Redis has no real
  acknowledgement, so `kombu` redelivers any message whose worker has not finished in time; shorter,
  and a slow task is handed to a *second* worker while the first is still running it. Both are
  constants in `app/jobs/celery_app.py` and a test asserts the ordering, because the failure is
  invisible until a job gets slow.
- **A metered vendor is paced by a `countdown` on a message, never by a sleep, and the unit of
  work is one call.** ANV-22's rule for any fan-out against a quota. The beat entry is a
  *dispatcher* that makes no vendor call at all: it asks one service for a plan and publishes one
  task per call with `countdown = index * spacing`. Three things follow and each is load-bearing.
  **One call per task**, so the spacing paces calls rather than batches — a task covering several
  calls could only space them internally, by waiting. **Nothing waits**: `time.sleep` holds a
  prefork child, and `await asyncio.sleep` is not the fix it looks like, because a Celery task owns
  its process for its whole duration and `run_async` gives it a loop with nothing else on it — a
  countdown costs nothing because the work does not exist yet. **The dispatched messages carry an
  `expires` too**, not just the beat entry they came from: a target nobody consumed is superseded by
  the next fan-out rather than run an hour late. The costs are stated rather than discovered: this
  is *pacing*, not a rate limiter (nothing counts calls, so two overlapping fan-outs double the
  rate — hence `max_calls × spacing` must stay inside the beat interval, and a test asserts it); a
  countdown is a *reservation* a worker holds in memory, so the fan-out is bounded rather than
  unbounded; and the plan is stale by the time its tail runs, so each task re-reads what it needs
  rather than trusting the plan. A real limiter (a shared token bucket) is a distributed lock's
  worth of machinery — reach for it when a job runs often enough that overlap is normal, not before.
- **Every beat entry carries an `expires` shorter than its interval.** Beat publishes whether or not
  anything is consuming, so a queue nobody is draining collects one message per tick and bringing
  the workers back replays hours of stale ticks at once. With an expiry there is at most one pending
  run of any job, and the next tick *is* the retry.

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
the top of a method, passed down). The same held for unwrapping a `SecretStr`
(`.get_secret_value()`) until ANV-17, which added the one documented exception: `app/clients/`
unwraps a vendor credential *inside its request builder*, keeps it in that stack frame and never
stores the plaintext — see the client section above for why a client, not its caller, has to be
the layer that holds the key.

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
  path decorator. **A literal segment is declared before a parameterised one** — `/users/me`
  above `/users/{user_id}` — because Starlette matches in declaration order and the reversed
  order turns `/me` into a failed attempt to parse `"me"` as a UUID.
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
- **A service may translate exactly the client failures that are not failures.** `app/clients/` has
  one exit and it is `ExternalServiceError` (→ 502), correctly: a client cannot know whether a
  missing object is an outage or an ordinary absence. A *service* can, so `StorageService` turns
  `details["reason"] == "object_not_found"` into `NotFoundError` and lets every other reason —
  denied, throttled, unreachable, misconfigured — through untouched, because those genuinely are
  "we are up, the upstream is not". The translation keys on the machine-readable `reason`, **never**
  on the message: that is the whole reason ANV-17 put a reason in `details`, and a message match
  breaks the first time a string is reworded. Translate the absence; never translate an outage into
  a 404, which would tell a user their data is gone.
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
  a free enumeration API. **Registration is the one deliberate exception** — a sign-up form on
  unique columns has to say *which* field clashed or it is unusable, and the only design that
  closes the leak ("always accept, then mail the address") needs a mail client we do not have.
  It is an exception because it creates something; a read must never be one.
- **A refusal that would confirm the resource exists is a 404, not a 403.** When a caller asks
  for a row that is real but not theirs, the answer is byte-identical to the answer for a row
  that does not exist — same status, same `code`, same `details` keys — and the service returns
  it *without querying*, so response time does not answer the question either. 403 is for an
  action the caller may not perform on something they can already see; "this belongs to somebody
  else" is not that. The rule applies to every owned resource (`GET /v1/users/{user_id}` today,
  watchlists next), and it is the other half of why a repo provides no "owned by" query:
  authorization is a service rule, and the shape of the refusal is part of that rule.
  Where the owner cannot be known without reading the row, "without querying" means **without
  querying the child**: fetch the parent alone (no eager load), compare, refuse — so a refusal
  never does work proportional to the size of a collection the caller may not see.
- **An owned resource's service has exactly one ownership gate, and every use case goes through
  it.** One private `async _resolve_owned(id, owner) -> Model` that fetches, compares
  `user_id` and raises the 404 above, returning the row so its callers have no reason to fetch
  it again. Not a `WHERE user_id = …` repeated per method, and not a FastAPI dependency: the
  defect ANV-15 fixed was one handler in a file of four having the clause and the others not,
  which is invisible in review and impossible to test for. A gate is a single call every method
  must make, so "does this endpoint check ownership" becomes one question instead of *n*.
  Its test is likewise one **parameterised sweep** over every use case, and the sweep's list of
  use cases is *derived from the service's public surface* (`vars(XService)`, minus a named
  exempt set) and asserted complete — so a use case added without an isolation test fails the
  suite rather than quietly going unchecked. Each case asserts three things: the status is 404
  and not 403; the body is identical to the body for an id that never existed; and no repo
  method beyond the gate's own lookup was reached.
- **A user-ordered collection stores a dense `0..n-1` `position`, and every mutation renumbers
  the whole list.** Append, insert, move and remove each take the current `{id: position}` map
  and return a complete new one, in `app/domain/<aggregate>.py`; the service applies it with a
  single `set_positions`. Renumbering totally rather than patching the rows that "should" have
  changed is what makes the rule correct when the stored ordinals have drifted — and they can,
  because `position` carries no unique constraint (a swap's intermediate state has to be legal)
  and nothing renumbers behind a caller's back. Two consequences: the move is keyed on the
  **entity id**, never on a client-supplied "current index", because the server knows where the
  row is and the client's belief is stale by construction; and an out-of-range destination is a
  422, never a clamp — clamping reproduces the old bug's shape, where a nonsense index produced
  a plausible-looking success. `(max_position or -1) + 1` is **not** the append rule: `0` is
  falsy, so it appends the second item on top of the first. Test `is None`.
- **A uniqueness pre-check is for the *message*; the unique index is for the *correctness*.**
  `email_exists`-style checks exist so a duplicate becomes a 409 naming the field a form has to
  fix (`details.field`), never so the insert may assume it is safe — two requests can both pass
  the check before either flushes. So a service that pre-checks **also** catches the
  `IntegrityError`, matches the constraint name (the deterministic `uq_<table>_<column>` from
  `Base.metadata`'s naming convention), and raises **the same** `ConflictError` the pre-check
  would have. Three parts are load-bearing: `await session.rollback()` first, because Postgres
  aborts the whole transaction on a constraint violation and refuses every later statement in
  it; the same error either way, so a client cannot tell "already taken" from "you were second";
  and an unrecognised constraint **re-raised untouched**, because that one really is a bug and a
  bug should be a 500. A conflict body never echoes the submitted value back. Only real Postgres
  can prove the constraint names match what the driver reports — that assertion belongs in
  `tests/integration/`, because a hand-built `IntegrityError` only tests itself.
- **An exception from `app/utils/` is translated by the service that called it.** `app/utils/`
  has no Anvex meaning and therefore cannot import `app/domain/` (§3), so its failures are plain
  builtins — `PasswordTooLongError` is a `ValueError`. The calling service is the only place
  that can turn one into a domain error, and it must: uncaught, a `ValueError` from a util is a
  500 for input the API should simply have refused. `UserService._hash` is the worked example
  (→ `ValidationError`, i.e. 422), and the path is genuinely reachable — a schema cap counted in
  characters does not enforce a library's limit counted in bytes.
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
- **The clock rule is really an *ambient input* rule, and entropy is the other one.** A `uuid4()`
  inside a domain function makes its output depend on something it was not given, exactly as a
  `datetime.now()` does, so a uniqueness token is a required keyword argument too and the service
  generates it beside the clock read. `app/domain/storage.export_key` is the worked example, and the
  payoff is the same: a test asserts the *whole* key rather than a regex over the interesting half.
  The purity sweep names `uuid4` alongside `now`.
- **`app/utils/security.py` owns the bcrypt 72-byte boundary**, because it is the only layer that
  knows the encoding: the schema cap is 72 *characters* and bcrypt's limit is 72 *bytes*, so a
  25-character multibyte password passes validation and still overflows. `hash_password` **raises**
  (a write we control — never persist a credential silently equal to its own prefix);
  `verify_password` **returns `False`** and never raises, for an over-long candidate and for a
  stored hash that is empty, foreign or corrupted. A broken hash fails one login, not the process.
  It calls the `bcrypt` package directly and states its cost factor (`BCRYPT_COST_FACTOR = 12`)
  rather than inheriting a library default, so an upstream change cannot move our work factor.
- **Lists return `Page[T]`, never a bare array.** `app.schemas.pagination.Page` is the one envelope:
  `{items, total, limit, offset, has_more}`, offset paging, `limit` bounded by `MAX_PAGE_LIMIT`.
  `total` counts every matching row, `has_more` is computed, and the two bounds are echoed so the
  response is self-describing. A bare array cannot gain a key later without breaking clients.
- **The `Page[T]` envelope is built in the service, and the limit is resolved there.** A repo
  returns `(rows, total)` and cannot import `app.schemas`, which is why `limit` is a *required*
  keyword on every paginated repo method — so the service resolves the caller's limit with
  `app.schemas.pagination.resolve_page_limit` (`None` → `DEFAULT_PAGE_LIMIT`, out of range → clamped
  into `1..MAX_PAGE_LIMIT`), passes that one number to the repo, projects the rows onto the `XOut`
  schema and echoes the same number back in the envelope. Two layers guard the ceiling and they are
  not redundant: the route's `Query(ge=1, le=MAX_PAGE_LIMIT)` **rejects** an over-large limit with a
  422, so an HTTP client is never quietly handed a shorter page than it asked for, while the
  service's clamp is what protects every caller that has no request to reject — a Celery task, a
  seed script — from asking Postgres for the whole table and then failing `Page.limit`'s own `le`
  bound with a 500. `total` is counted *before* the window, so an offset past the end is an empty
  page with a truthful total, never an implied end of the collection.
- **`offset` is clamped where `limit` is clamped, and refused where `limit` is refused.** The route
  carries `Query(ge=0)` so an HTTP client is never quietly moved to a window it did not ask for,
  while the service-side resolution floors a negative offset at `0` for the callers with no request
  to reject — a job whose page arithmetic went negative gets the first page rather than a SQL error
  nobody reads. Same two-layer argument as the limit ceiling; keep the pair together.
- **A pure rule with a second caller moves *down* to `app/domain/`, never sideways.** Two services
  importing each other is the wrong shape — it makes them impossible to test apart, and it hides a
  rule in whichever one happened to need it first. Move the function into `app/domain/<aggregate>.py`
  and have both import it downward; leave a re-export behind in the original module (`__all__` keeps
  it honest) so the established import path and its tests keep working, and assert in a test that
  the re-export **is** the same object rather than a copy. ANV-14 did this to ANV-13's
  `normalise_ticker`. **The rule applies a second time *inside* `app/domain/`**: a rule living in
  `app/domain/<aggregate>.py` that acquires callers from other aggregates belongs in an
  aggregate-neutral module, because a rule three aggregates share belongs to none of them and the
  alternative is two domain modules importing each other. ANV-16 moved `resolve_window` (with
  `PageWindow` and `MIN_OFFSET`) from `app/domain/stock_data.py` to `app/domain/pagination.py` on
  its third caller, leaving the re-export behind. Trigger on the *second* caller, move on a
  cross-aggregate one.
- **Normalising an identifier is the service's job, not the request schema's.** An annotated type's
  `BeforeValidator` (ANV-8's `Ticker`) does apply to a **path** parameter as well as a body — that
  was verified, not assumed — but it only ever covers callers that arrive over HTTP. A rule that a
  Celery task or a seed loader must also obey belongs in the one layer they all go through, so the
  route passes the raw path segment down and the service canonicalises it. The corresponding repo
  lookup stays exact and case-sensitive: folding case in the query would defeat the unique index it
  is served by. Two tests keep this honest — the OpenAPI document is asserted to declare a plain
  unconstrained string (nothing is happening at the edge) and the lower-cased URL is asserted to
  resolve anyway (so it can only be the service).
- **The literal-before-parameterised rule bites within *one* path segment.** `/{x}` compiles to a
  single-segment pattern — Starlette's default converter never matches a `/` — so a two-segment
  literal like `/stocks/by-ticker/{ticker}` cannot be shadowed by `/stocks/{stock_id}` whichever
  order they are declared in, while a one-segment literal like `/users/me` absolutely can. Declare
  the literal first regardless, and if a test claims the ordering is load-bearing, make it prove
  that against a control app with the declarations reversed rather than asserting a comment.
- **A collection that only means anything inside a parent is nested under it, and a missing parent
  is a 404.** `GET /v1/stocks/{stock_id}/data`, not `GET /v1/stock-data?stock_id=…`: the parent is
  then not omittable, so there is no default that silently means "every parent's rows interleaved",
  and the two ways of naming the parent are the ones its own resource already established
  (`/{id}` and `/by-ticker/{ticker}`) rather than a third convention. The service resolves the
  parent **before** querying the child and raises `NotFoundError` when it is absent — a repo cannot
  make that call, because "no such parent" and "this parent has no rows" are the same `([], 0)` to
  it. A sub-collection of a parent that does not exist is a 404; an existing parent with nothing to
  show is a 200 with an empty page. (Where the parent is *owned*, §4's "refuse with a 404, not a
  403" rule applies on top of this; reference data like a security has no owner and the 404 is the
  plain kind.)
- **The same rule holds when the child lives at a vendor, and there the local row earns its keep
  twice.** `GET /v1/news/by-symbol/{ticker}` resolves the ticker against `StockRepo` *before*
  calling NewsAPI: the vendor answers a nonsense symbol with `{"status": "ok", "totalResults": 0}`,
  byte-identical to a real company nobody wrote about this week, so **only the local table can tell
  a typo from a quiet week** — if the service does not ask, the question can never be answered. It
  also stops a metered quota being spent on garbage, and — the reason that actually decides it —
  the local row carries the *company name*, so resolving is not a precondition of a good vendor
  query, it **is** the good query (`q="CAT"` returns articles about cats; `q="CAT" OR "Caterpillar
  Inc."` does not). Querying the vendor blind would mean shipping the worse product to avoid a 404.
  A URL is not obliged to be nested to obey this — `/v1/news/by-symbol/{ticker}` is a news resource
  keyed by a symbol, not a sub-collection of a security — but the 404-vs-empty-page rule is the same.
- **A collection served entirely from a client still returns `Page[T]`, and `total` counts what
  `offset` indexes into.** A vendor's own match count is not that number when anything is filtered
  or de-duplicated after the fetch, and forwarding it produces a `total` of 3,412 above a collection
  that ends at 74 and a `has_more` that lies on the last page. So the service fetches one vendor
  page at the vendor's maximum, applies the domain rule, and windows the *result* itself — `total`
  is the length of the list the window is taken from, and the envelope is self-consistent. This is a
  deliberate ceiling, not upstream paging: a caller that needs the archive wants a different
  endpoint. Nothing is persisted on the way through — a third-party document with its own lifecycle
  buys a cache and a staleness problem, so there is no repo and no table unless a ticket adds one.
- **Two routers may share a URL prefix; they do not share anything else.** A nested sub-collection
  keeps its own `app/services/x.py`, `app/deps/x.py` (its own `get_x_service` seam), router module
  and `tags`, even when its router declares the same `prefix` as its parent's. The layering is what
  matters, not the URL — and one seam per service is what lets an API test stub the child without
  also replacing the parent's endpoints. Include the parent's router first in `app/api/v1/
  __init__.py`, so it wins any path both routers could claim.
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
├── styles/
└── test/         # setup.js (the one setupFiles entry) + msw/{handlers,server}.js
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

### How the layer runs (ANV-23)

**`frontend/README.md` is the operating manual; this is the contract.**

- **Node is not installed on the dev host, by choice, and that is not a temporary state.**
  Every `npm` / `vite` / `vitest` / `eslint` invocation runs in a container built from
  `frontend/Dockerfile` — either `docker compose exec web npm run <script>` against the running
  `web` service, or a one-shot `docker run … anvex/web:dev npm run <script>`. **A one-shot mounts
  the *repo root*, not `frontend/`**, because `envDir` reaches one level up for the `.env`.
- **`node_modules` lives at `/node_modules`, above the `/app` bind mount.** This is §4's image-layout
  rule applied to Node: compose mounts `./frontend` over `/app`, so anything installed inside `/app`
  is hidden, exactly as a venv at `/app/.venv` would be. Node resolves by walking *up* from the
  importing file and `npm run` puts every ancestor `node_modules/.bin` on `PATH`, so `/node_modules`
  is the one location that works from both `/app/src/**` and `/repo/frontend/src/**`. **No
  `node_modules` volume**, deliberately: the image layer is authoritative, a dependency change is
  `up -d --build`, and a stale anonymous volume can never silently outrank the lockfile. Vite's
  `cacheDir` is `/tmp/anvex-vite` for the same reason `beat` writes its schedule to `/tmp` — nothing
  a container generates belongs in the bind-mounted source tree.
- **Never set `NODE_ENV` in a frontend image or environment.** Vite honours an inherited `NODE_ENV`
  over its own mode, so `NODE_ENV=development` — which is the *obviously correct* thing to put in a
  dev stage — makes `npm run build` bundle `react-dom.development` and emit a development build with
  no warning at all (330 kB vs 145 kB). Vite sets it itself, per command. The Dockerfile carries a
  comment where the `ENV` line would go.
- **Config comes from the root `.env` and nowhere else.** `vite.config.js` sets `envDir` to the repo
  root (§2), and only `VITE_`-prefixed keys reach the browser. Under compose the same values also
  arrive as process env via `env_file`, which Vite prefers — both paths lead to one file. **There is
  no `frontend/.env`, and the old app's `src/app-config.json` is not ported.** `src/lib/env.js` is
  the only module that touches `import.meta.env`; everything else imports `API_BASE_URL` / `apiUrl`
  from it. A config value the *browser* must not see (the dev proxy target) carries no `VITE_`
  prefix and is read at config time via `loadEnv(mode, root, '')`.
- **Two ways to reach the API, both supported.** `VITE_API_BASE_URL` set → cross-origin, allowed by
  `API_CORS_ORIGINS`. `VITE_API_BASE_URL` empty → same-origin: `apiUrl()` yields relative URLs and
  the dev-server proxy forwards `/v1` and `/health` to `WEB_DEV_PROXY_TARGET` (the `api` service by
  name inside compose). Empty is a meaningful value, not a missing one.

### Tailwind is **v3**, and that was a decision (ANV-23)

The config is carried over token-for-token from `AverageInvestorWeb/tailwind.config.js`. The old
repo declared `tailwindcss ^4` but **never made it work** — no `postcss.config.js`, no
`@tailwindcss/postcss`, and v3 `@tailwind` directives in its CSS — so there was no v4 setup to
preserve, only a v3-shaped config file. v4 additionally moves configuration into CSS (`fontWeight`
is not even a theme namespace there) and changes defaults the ported components were authored
against: border colour → `currentColor`, ring → 1px `currentColor`, `shadow-sm` renamed,
`outline-none` → `outline-hidden`. Adopting it alongside the scaffold would restyle all ~40
components ANV-28..36 port, invisibly. **v4 is its own ticket, after the ports land.**
`src/styles/tailwind.test.js` runs the real PostCSS pipeline and asserts on the *generated CSS* —
a design token that stops being emitted fails the suite rather than showing up as an unstyled screen.

### Frontend test harness (ANV-23)

The mirror of §6's backend rules: **extend the one setup file and the one MSW server; never start a
second.**

- `src/test/setup.js` — the only `setupFiles` entry. Installs `@testing-library/jest-dom`, starts
  the server, and runs `cleanup()` + `resetHandlers()` after **every** test.
- `src/test/msw/server.js` — the only `setupServer(...)` call in the repo. A second one means two
  interceptors fighting over `fetch`.
- `src/test/msw/handlers.js` — the defaults, plus `errorResponse()` and `pageResponse()`. **Build a
  mock through those two**, so a handler cannot invent a body the backend would never send: the
  error envelope is fixed (`{error: {code, message, details, request_id}}`, `details` `{}` and never
  `null`) and a list is always `Page<T>`. Handlers are keyed on `apiUrl(path)`, so they follow
  `VITE_API_BASE_URL` and cover both the cross-origin and same-origin configurations.
- One test's worth of behaviour is `server.use(...)`, never an edit to the defaults.
- **`onUnhandledRequest: 'error'`.** The default (`'warn'`) lets an unmocked call reach the real
  network, where it hangs or — worse — hits an API the developer happens to be running, and the test
  passes for the wrong reason.
- **`fetch`/`axios` are never stubbed.** Mocking at the network boundary is what keeps the API client
  itself (interceptors, refresh, error mapping) under test rather than mocked away.
- vitest stubs `.css` imports (`?raw` included) unless `test.css` is on, and `import.meta.url` is an
  `http:` URL inside vitest — a test that needs a file's text reads it from disk relative to
  `process.cwd()`.

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
| `minio_available` | session | skips unless the compose `minio` container answers |
| `s3_bucket` | function | a brand-new bucket, emptied and dropped afterwards |
| `s3_settings` | function | `Settings` pointed at host-side MinIO and that bucket |
| `s3_client` | function | a real `S3Client` on the container, closed at teardown |
| `broker_available` | session | skips unless the compose `redis` container answers |
| `broker_app` | function | the application `celery_app`, repointed at Redis databases 14/15 and flushed either side |
| `broker_worker` | function | `broker_app` plus a real Celery worker consuming in a thread of this process |

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
- **Eager mode proves the task body; only a broker proves the wiring.** `task_always_eager` runs a
  task by calling it — nothing is serialised, no connection is opened, no worker is involved, and it
  keeps passing when the broker URL is nonsense. So a job ships with both: eager tests in
  `tests/unit/` for the body, and `tests/integration/` tests that publish to real Redis and let a
  real consumer pick the message up. The broker tier uses Redis **databases 14 and 15**, not the
  app's 0 and 1, so a `worker` the developer happens to have running cannot eat a message the tests
  published — which would present as a test hanging on a result that never arrives. A stub task
  registered for a test passes **`shared=False`**: `@app.task` defaults to `shared=True`, which
  registers it on every Celery app finalized afterwards, including the application's.
- **A new container tier is three things, and it copies the database tier exactly.** A `tests/<x>.py`
  module holding the connection details (its own test-only `BaseSettings` on the same repo-root
  `.env`, a `@cache`d `unavailable_reason()` where *every* failure is a skip, and the create/drop
  helpers), a `<X>_FIXTURES` frozenset in `conftest.py` that `pytest_collection_modifyitems` turns
  into a marker, and fixtures that hand the test its **own** namespace. `tests/storage.py` is the
  worked example: isolation is a throwaway bucket rather than a rolled-back transaction, because S3
  has no transaction — but the shape, the skip and the "one number in `.env` moves both the compose
  mapping and the client" rule are identical. `tests/broker.py` (ANV-21) is the third and copies it
  again; isolation there is a flushed pair of dedicated Redis databases. Nothing in `app/` learns
  the tier exists.
- **A tier that skips in both runs proves nothing**, so a ticket adding one reports the count that
  actually executed with the container up (`-m <marker>`), not just that the suite was green.
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
