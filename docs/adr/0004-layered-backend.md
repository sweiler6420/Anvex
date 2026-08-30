# ADR-0004 — A layered backend with one-way dependencies

## Status

**Accepted** — ANV-1, with the concrete service/dependency/handler shape fixed by ANV-11.
Recorded in ANV-39.

## Context

`AverageInvestorApi` put SQLAlchemy queries, business rules and HTTP concerns in the same
functions. The defect that decided this ADR is worth writing out, because it is not
hypothetical and it is not exotic:

In `AverageInvestorApi/api/routers/watchlist.py`, `get_watchlist` filtered its query on
`user_id` and `reposition_stock` did not. Same file, four handlers, one `WHERE` clause on
one line present in some of them and absent in others. `current_user` was injected into the
reorder handler and then never read. So any authenticated caller could reorder — and, via
`add_watchlist_stock`, add to — **anybody's** watchlist, and since registration is
self-service, "any authenticated caller" is "anybody at all".

That bug is invisible in review, because the correct line and the missing line look
identical from a diff of the file that has it. And it is untestable in the abstract, because
there is no single place to assert "this endpoint checks ownership".

## Decision

`backend/app/` is split into directories with exactly one job each, and **dependencies flow
downward only**: a layer may import from layers below it, never above.

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

with `deps/` wiring, `schemas/` describing the public shape, `middleware/` handling
cross-cutting request concerns, `jobs/` holding Celery entry points, `data/` holding
checked-in seed data, and `utils/` holding helpers with no Anvex meaning.

The rules that matter operationally: a handler accepts a validated request, calls **one**
service method and returns a schema; a service is the only layer allowed to talk to several
others, to own a transaction, and to read a clock; a repo takes a session and never holds
one, and never commits; `app/domain/` does no I/O of any kind; a client knows one vendor and
nothing about Anvex, and leaves by exactly one exception.

The full argument for every rule is in [`../../CLAUDE.md`](../../CLAUDE.md) §3, which is the
contract; this record is why there is one.

## Consequences

**An ownership check becomes one question instead of *n*.** `WatchlistService` has exactly
one private `_resolve_owned(id, owner)` that fetches the row, compares `user_id`, raises a
404 that is byte-identical to "no such watchlist", and returns the row so its callers have
no reason to fetch it again. Every use case starts there — you cannot reach the entries
without having passed it. Its test is a **parameterised sweep whose case list is derived
from `vars(WatchlistService)`** and asserted complete, so a use case added without isolation
coverage fails the suite rather than quietly going unchecked. That test is the thing the old
layout could not have.

**Seven files for one endpoint.** Adding `PATCH /v1/watchlists/{id}/stocks/{stock_id}`
touched `app/schemas/`, `app/models/`, `app/repos/`, `app/domain/`, `app/services/`,
`app/deps/` and `app/api/v1/` — walked line by line in
[`../../backend/docs/adding-an-endpoint.md`](../../backend/docs/adding-an-endpoint.md). That
is the cost, it is paid on every feature, and it buys the reuse: the same service method
serves a route and a Celery task.

**The rules are enforced by machines, not by review.** `app/clients/` is swept by an AST
parser that fails on an import of `sqlalchemy`, `requests`, `app.repos`, `app.db`,
`app.models`, `app.services`, `app.schemas`, `app.api` or `app.jobs`, on a `time.sleep`, and
on a bare `print`. Each domain module's tests parse its own source and fail on a clock read
or a `uuid4`. A convention that lives only in prose gets broken.

**A rule with a second caller moves *down*, never sideways**, and it happened twice.
Two services importing each other is the wrong shape — it makes them impossible to test
apart and hides a rule in whichever one needed it first. `normalise_ticker` moved from
`app/services/stock.py` into `app/domain/stock.py` on its second caller (ANV-14), and
`resolve_window` moved from `app/domain/stock_data.py` into `app/domain/pagination.py` on its
third (ANV-16) — the same rule applied a second time *inside* the domain layer, because a
rule three aggregates share belongs to none of them. Each move left a re-export behind so
the established import path and its tests kept working, with a test asserting the re-export
**is** the same object rather than a copy.

**`CLAUDE.md`'s worked example describes code that was never built, and the code is what is
authoritative.** §3's "adding sync a stock's news" walkthrough names a news repo module, a
`NewsService.sync_for_stock(symbol)` method, a `POST /v1/news/sync` route and a news job
module. None of the four exist. News is served straight from the vendor per request, with
**no repo and no table** —
a decision taken in ANV-19 on the grounds that a third-party document with its own lifecycle
buys a cache and a staleness problem. The §3 example is illustrative of the *shape*, written
before any of it existed, and it should be read that way. This is the one place where the
contract document and the repository disagree.
