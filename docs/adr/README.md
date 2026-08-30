# Architecture decision records

One record per decision that would otherwise have to be reconstructed from a diff. Each one
states the context **as it actually was** — including the premises that turned out to be
wrong and the reversals — the decision taken, and the consequences accepted, so that a
future reader can tell a deliberate cost from an oversight.

These were written in ANV-39, after the fact, from `../build-log.md`, `../ticket-log.md`,
`../../CLAUDE.md` and the code. Where a source document and the code disagreed, the code
won and the disagreement is recorded in the ADR.

## The records

| # | Title | Status |
| --- | --- | --- |
| 0001 | [Monorepo, replacing three repositories](./0001-monorepo-over-three-repositories.md) | Accepted |
| 0002 | [Async everywhere in the backend](./0002-async-first-backend.md) | Accepted |
| 0003 | [uv, and no `requirements.txt`](./0003-uv-over-pip.md) | Accepted |
| 0004 | [A layered backend with one-way dependencies](./0004-layered-backend.md) | Accepted |
| 0005 | [Postgres and S3 as the two data stores](./0005-postgres-and-s3.md) | Accepted |
| 0006 | [TanStack Router, not react-router](./0006-tanstack-router.md) | Accepted |
| 0007 | [CI calls the repository's own commands, asymmetrically](./0007-ci-calls-the-repos-commands.md) | Accepted |
| 0008 | [A path filter is coverage, so the backend filter reaches outside `backend/`](./0008-path-filter-is-coverage.md) | Accepted |
| 0009 | [No secret value in Terraform; secrets are created empty](./0009-no-secrets-in-terraform.md) | Accepted |
| 0010 | [`.tfvars` are committed, and `local` is not a deployment](./0010-committed-tfvars-and-local.md) | Accepted |
| 0011 | [One image and one ECR repository for api, worker and beat](./0011-one-image-one-ecr-repository.md) | Accepted |

## Writing a new one

Copy the shape, do not invent one. Every record has an `# ADR-NNNN — Title` heading whose
number matches its filename, and exactly these four sections in this order:

```
## Status
## Context
## Decision
## Consequences
```

`backend/tests/unit/test_docs.py` asserts the numbering is sequential from 0001 with no
gaps and no duplicates, that the four sections are present and in order, that every record
is listed in the table above, and that every entry in the table above is a record. Add the
row in the same commit as the file.

Anything that does not fit a home in the repository layout does not get a new top-level
folder without a record here — that rule is in `../../CLAUDE.md` §1, and this is where it
points.
