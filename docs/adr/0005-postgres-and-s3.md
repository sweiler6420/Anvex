# ADR-0005 — Postgres and S3 as the two data stores

## Status

**Accepted** — ANV-1, with S3 landing in ANV-20. Recorded in ANV-39.

## Context

The data Anvex holds falls into two shapes and no more.

The first is relational and has real constraints: an account owns watchlists, a watchlist
holds an *ordered* set of securities, a security has candles that must be unique per
`(stock_id, date, time)` and must not lose the fourth decimal place of a price. Every one of
those is a constraint a database can enforce and application code cannot enforce reliably —
the predecessor proves it, because its `watchlist_data` had no real primary key and the same
stock could be added twice.

The second is blobs: exports, generated on request, read once, and worth nothing after a
month. A row is the wrong shape for a megabyte, and a filesystem is the wrong shape for
anything that has to survive a container.

The predecessor already used Postgres, so the choice was really whether to keep it, and
whether object storage was worth introducing before anything needed it.

## Decision

**Postgres** for everything relational, in a dedicated `anvex` schema, with Alembic as the
only source of schema. `Base.metadata` carries `schema="anvex"` so no model sets
`__table_args__ = {"schema": ...}`, and `Base.metadata.create_all` is banned outside tests.

**S3** for objects — MinIO locally, the same API in AWS — reached through
`backend/app/clients/s3.py` and used only by `backend/app/services/storage.py`. Keys are
built by `backend/app/domain/storage.py`.

**Redis is not a data store.** It is the Celery broker (database 0) and result backend
(database 1), and nothing else reads or writes it. There is no cache layer.

## Consequences

**`alembic check` is a gate.** "No new upgrade operations detected" is the contract between
`backend/app/models/` and `backend/app/db/migrations/`, asserted by the test suite as well
as available on the command line. Autogenerate output is a draft — review and reformat it,
never change what it *does* without re-running the check.

**The dedicated schema cost one non-obvious fix.** The login role is also called `anvex`, so
Postgres' stock `"$user", public` search path made `anvex` the *default* schema — and
Alembic represents the default schema as `None`, which broke reflection, every foreign-key
comparison and the `alembic_version` exclusion. `env.py` pins `search_path` to `public` on
its own connection. Without that pin, autogenerate can never produce an empty diff.

**Constraint names are deterministic, and a service depends on that.** `Base.metadata`
carries a naming convention (`pk_` / `fk_` / `uq_` / `ix_` / `ck_`), so a service catching an
`IntegrityError` matches on `uq_<table>_<column>` or `pk_watchlist_data` rather than on a
driver message. That is what lets a uniqueness pre-check ("give the form a 409 naming the
field") coexist with the index that is actually correct under concurrency, raising the *same*
error either way so a client cannot tell "already taken" from "you were second".

**Testing against a real Postgres is mandatory for some assertions and optional for most.**
Only real Postgres can prove that the constraint names match what the driver reports, so
that assertion lives in `backend/tests/integration/`. Everything else pushes down to the
unit tier against in-memory fakes. The database tier skips politely when `db-test` is not
running, which is why CI has to assert the tier is *reachable* before running the suite —
a skip is not a pass.

**S3 has no transaction, so the test tier's isolation is a throwaway bucket** rather than a
rolled-back transaction. That difference is the whole reason `backend/tests/storage.py`
exists as its own module beside `backend/tests/database.py` rather than reusing it; the
shape, the skip and the "one number in `.env` moves both the compose mapping and the client"
rule are otherwise identical.

**The object store has no caller in the running application.** `StorageService` is complete
and tested, `backend/app/deps/storage.py` builds one per request, and no router asks for
it — including `download_url`, whose presigned URL's query string *is* a credential until it
expires. Whether such a URL should leave the API is an open decision, recorded as one in
[`../architecture.md`](../architecture.md) §6 rather than left to look like an oversight.

**`S3Client` cannot talk to real AWS S3 yet.** `Settings.s3_endpoint_url` defaults to the
MinIO URL, so it cannot be unset by omitting the variable, and `""` is not `None` to
botocore; and `_require_configuration` refuses a blank key pair deliberately, because
otherwise botocore falls back to the ambient credential chain and a laptop with stale AWS
credentials would quietly write to a real bucket. Both properties are individually correct
and combine badly. It is tracked as `TODO(ANV-s3-aws)` and is the reason the Terraform
creates an IAM *user* rather than granting the Fargate task role.

**Redis staying a broker keeps one failure domain out of the request path.** The API process
opens no Redis connection at all, so the queue being down cannot affect a read. The cost is
that there is no cache: `/v1/news/*` calls the vendor on every request, bounded by the
vendor's quota rather than by anything of ours.
