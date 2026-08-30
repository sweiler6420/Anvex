# ADR-0007 — CI calls the repository's own commands, asymmetrically

## Status

**Accepted** — ANV-38. Recorded in ANV-39.

## Context

ANV-37 put every developer command in `scripts/` as a `.ps1`/`.sh` pair — `up`, `down`,
`logs`, `migrate`, `makemigration`, `seed`, `test`, `lint`, `fmt`, `reset-db` — precisely so
that "run the backend suite" has **one** implementation. Those wrappers are not thin: they
resolve the repository root, resolve `uv` (which is not on `PATH` on the development
machine), clear a stale `VIRTUAL_ENV`, invoke `python -m pytest` rather than the blocked
console script, and translate the compose service name `db` into `localhost` and the
published port for host-side database tooling.

A workflow that writes `uv run python -m pytest` in a `run:` step is the second
implementation that ticket existed to delete — and it is the copy that would silently drop
one of those traps, because a Linux runner does not hit them and nothing would fail.

The frontend wrappers are not like that. `scripts/lint.sh frontend` is
`docker compose --profile frontend up -d --no-deps web` followed by
`docker compose --profile frontend exec -T web npm run lint`. Everything it adds is the
container, and the container exists for exactly one reason: **there is no node on the
development host, by choice.** A GitHub runner has node.

## Decision

The **backend** job calls `./scripts/lint.sh backend` and `./scripts/test.sh backend`, and
nothing else. `backend/tests/unit/test_ci_workflow.py` fails on a `run:` step that names
`pytest`, `ruff` or `alembic` directly.

The **frontend** job calls `npm run lint`, `npm run test` and `npm run build` from
`frontend/`, bypassing the `scripts/` wrappers entirely.

The asymmetry is deliberate and is the decision, not an inconsistency to be tidied later.

## Consequences

**Every backend trap is encoded once and tested once.** `python -m pytest` and never the
console script, the cleared `VIRTUAL_ENV`, the uv resolution — all in `scripts/`, all
asserted by `backend/tests/unit/test_repo_scripts.py`, which additionally parses both halves
of every pair with the real shell parsers and fails on drift between them.

**The frontend's traps have to live in `package.json` instead**, so the repository's "one
definition of a command" is in two different files depending on the stack. The most
important of those traps — **nothing may set `NODE_ENV`**, because Vite honours an inherited
one over its own mode and silently ships a development bundle with exit code 0 — cannot be
encoded in a script at all, since its failure mode is an environment variable that is
*present*. It is enforced instead by a tripwire step that counts `jsxDEV` occurrences in
`dist/` and fails on anything but zero.

**Running the frontend through the wrappers in CI was rejected on cost, not on principle.**
It would mean building `anvex/web:dev` and starting a compose service on a runner that
already has node, to gain nothing — and `docker pull` on the development machine has an
intermittent DNS failure against `production.cloudfront.docker.com`, so the container path
is the *less* reliable one in both places.

**The workflow is parsed by a backend test, which is only possible because of
[ADR-0001](./0001-monorepo-over-three-repositories.md).** 64 tests in
`backend/tests/unit/test_ci_workflow.py` assert the jobs, the triggers, the pinned action
SHAs with a `# vX.Y.Z` comment beside each, the filters, and that every `./scripts/*.sh` the
workflow names actually exists.

**Two mutants survived the first verification pass, and both improved the suite.** Replacing
the service-reachability probe with `: <<'PY'` — a heredoc fed to the null command — left
every word of the probe in the file and executed none of it. Hard-coding `count=0` in the
bundle check left the step's own `echo` line saying `jsxDEV`. Both tests now assert the
**mechanism** (`uv run python`, `grep -c 'jsxDEV'`) rather than the vocabulary. That is the
general lesson and it recurred in ANV-40: *a test that matches prose in a header passes for
an implementation that deleted the thing the prose was about.*

**A skip is not a pass.** Every service tier in the backend suite skips politely when its
service is absent, which on a runner would turn a wiring mistake into a green tick over a
suite that tested nothing. The backend job therefore asserts
`tests.database.unavailable_reason()` and `tests.broker.unavailable_reason()` are both
`None` *before* running the suite, and asserts `pwsh` is present so the `.ps1` parser tests
execute rather than skip. **Any new tier that can skip needs the same two lines** — and the
one tier that genuinely cannot run there, MinIO, is documented as skipping rather than
quietly absent.
