# ADR-0001 — Monorepo, replacing three repositories

## Status

**Accepted** — ANV-1. Recorded in ANV-39.

## Context

Anvex replaces three separate repositories, all still on disk and all read-only history:

- **`AverageInvestorApi`** — sync FastAPI on SQLAlchemy 1.4, `avg_inv` Postgres schema,
  configuration in `settings/config.{env}.json`. Tables `users`, `stocks`, `stock_data`,
  `watchlists`, `watchlist_data`, `politicians`; routers for auth, users, stocks, stock
  data, watchlists and a news endpoint that returned a hardcoded blob.
- **`AverageInvestorService`** — a standalone AlphaVantage → pandas → Postgres ETL with
  EC2 and Lambda deployment glue around it.
- **`AverageInvestorWeb`** — Create React App, React 18, Tailwind, react-router v6, an
  axios refresh interceptor, and a ~1,200-line bin-packing window system.

The three shared a database and agreed about nothing else. The API and the ETL each carried
their own model definitions of the same tables. The web app's idea of the auth contract was
maintained by reading the API's source. A change to a response shape was three pull requests
in three places with no way to test the seam, and nothing in any repository could fail
because another one had changed.

The alternative considered was keeping three repositories and adding a shared contract
package or generated client. That fixes the schema half and none of the behaviour half: it
would not have caught a client-side password policy diverging from a server-side one,
because neither side's policy is in a schema.

## Decision

One repository. `backend/` (FastAPI, Celery, Alembic, pytest), `frontend/` (Vite, React,
TanStack Router, vitest), `docs/`, `scripts/`, and **one `.env` at the root that every
stack reads** — compose injects it, and Vite's `envDir` reaches one level up to find the
same file. There is no per-stack environment file, and no second `.env` inside `frontend/`.

`AverageInvestorService`'s *concept* is deliberately gone rather than ported: its ETL is
now Celery jobs in `backend/app/jobs/` on top of a client in `backend/app/clients/`, and
its EC2/Lambda glue is replaced by a worker container and a beat container.

The three old repositories are reference only and are never modified.

## Consequences

**A cross-stack guard becomes possible, and that is the payoff that justifies the layout.**
`backend/tests/unit/test_domain_password.py` reads
`frontend/src/features/auth/components/SignUpPage.jsx` and fails when the client's password
rules and `backend/app/domain/password.py` disagree — ids, order, both human phrasings, and
the predicates pinned as source strings where Python cannot execute a JS regex. That test is
not expressible across three repositories at all. Several other tests do the same thing in
the small: the CI workflow is parsed by a backend test, so are the `scripts/` pairs, so is
`.env.example`, and so is the Terraform.

**The cost is that "which stack changed" stops being obvious**, and CI has to answer it with
a path filter — which then becomes its own hazard, because a filter that skips a job
silently deletes the coverage that job held. See [ADR-0008](./0008-path-filter-is-coverage.md).
A frontend-only commit that edits `SignUpPage.jsx` runs the backend job, deliberately.

**One history, one review surface, one `.env`** — and one place a mistake reaches
everything. The `.env` rule in particular is a real constraint: every new key must be added
to `.env.example` in the same commit, a test asserts the two agree, and host-side database
tooling has to translate the compose service name `db` to `localhost` and the published port
rather than being handed a second DSN.

**Nothing was migrated.** This is a net-new build that ports behaviour, not a
`git filter-repo` of three histories: the old commits are not in this repository, and a
`git log` here starts at ANV-1. The old code is quoted in docstrings where a defect was
being designed out — which is why several modules name the file they replaced — but it is
not an ancestor of anything here.

**`AverageInvestorService` can be deleted**, with two things it did that Anvex does not:
deep historical backfill (its 43-month sweep) and the EC2/Lambda deployment glue. The second
is deliberately gone. The first is a gap and is recorded as one in
[`../architecture.md`](../architecture.md) §6.
