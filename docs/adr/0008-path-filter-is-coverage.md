# ADR-0008 — A path filter is coverage, so the backend filter reaches outside `backend/`

## Status

**Accepted** — ANV-38. Recorded in ANV-39.

## Context

A monorepo ([ADR-0001](./0001-monorepo-over-three-repositories.md)) makes "which stack
changed?" a question CI has to answer. GitHub's `on.<event>.paths` filters the **workflow**,
not a job, so per-stack gating needs a `changes` job that computes the answer and two `if:`s
that consume it.

The obvious filter is `backend/**` for the backend job and `frontend/**` for the frontend
one. It is wrong, and the way it is wrong is the reason this record exists.

**A path filter can delete coverage with no test edited and nothing to see in the diff.**
`backend/tests/unit/test_domain_password.py` reads
`frontend/src/features/auth/components/SignUpPage.jsx` and fails when the client's password
rules and `backend/app/domain/password.py` disagree. Under a `backend/**` filter, the
backend job would be **skipped on precisely the commit that edits the client rules** — that
is, on the only kind of change the guard exists to catch. The guard would stop guarding,
silently, and the run would be green.

The same argument holds, less dramatically, for every other file a backend test reads:
`scripts/`, `README.md`, `CLAUDE.md`, `.env.example`, `docker-compose.yml`, `.gitignore` and
`docs/aws-deployment.md`. Each of those assertions is decoration if editing the file does not
run the suite that makes it.

## Decision

**Treat a filter entry as coverage.** The backend filter lists every repository-root file a
backend test reads, in addition to `backend/**`.

And, because a hand-maintained list of those is a list that will be forgotten: the *test*
discovers the list. `backend/tests/unit/test_ci_workflow.py` scans every `.py` under
`backend/tests/` for the pattern `REPO_ROOT / "…" / "…"`, turns each into a repository-root
path, and asserts the backend filter matches it — parameterised, so each uncovered path is
its own named failure.

A mirror set (`NOT_BACKEND`, `NOT_FRONTEND`) asserts what must **not** match, so a filter
that has quietly widened to "everything" fails too.

## Consequences

**Adding a test that reads outside `backend/` means adding its path to the filter, and the
suite tells you** — but it tells you *after* you have written the test, not before. That is
the accepted ergonomics: the alternative is a comment nobody reads.

**A frontend-only commit that touches `SignUpPage.jsx` runs the backend job.** That is the
whole point, and it will look like a misconfiguration to someone who has not read this.

**The filter grows over time, and the growth is visible.** ANV-40 added `.gitignore` and
`docs/aws-deployment.md`, making that file the first `docs/` entry — so editing the AWS cost
estimate runs the backend suite, because a test asserts the instance classes it prices are
the ones `backend/infra/envs/dev.tfvars` configures. ANV-39 added `docs/architecture.md` and
`docs/adr/**` for the documentation drift tests. `docs/build-log.md` and `docs/ticket-log.md`
are still in neither filter and still run nothing, and there are assertions that they do not.

**The matcher only understands literal paths and `dir/**`, and that is asserted.** A test
fails on any pattern more elaborate than those two shapes, on the grounds that a matcher
which mis-models the language it is checking reports the configuration is correct when it is
not — the same argument that brought `pyyaml` in to parse the workflow rather than a regex,
and `python-hcl2` to parse the Terraform.

**The escape hatch is explicit.** `.env` is gitignored, so it can never appear in a diff and
a filter entry for it would match nothing ever. It is listed in `UNFILTERABLE_PATHS` with
that reason written down, and anything else appearing there is a bug in the filter rather
than a licence.

**A skip is still not a pass** — the other half of the same idea, at the tier level rather
than the job level, and it lives in [ADR-0007](./0007-ci-calls-the-repos-commands.md).
