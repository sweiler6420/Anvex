# ADR-0003 — uv, and no `requirements.txt`

## Status

**Accepted** — ANV-1/ANV-2. Recorded in ANV-39.

## Context

`AverageInvestorApi`'s dependency story was a `requirements.txt` added in the last commit
before the rewrite, largely unpinned, with no lock file. Reinstalling it resolved to
whatever PyPI held that day, which is the failure mode where "works on my machine" is
literally true and unreproducible.

The choices were pip plus `pip-tools`, Poetry, or uv. All three produce a lock file. The
argument for uv was the resolver's speed on a machine where the whole suite is run several
times per ticket, one tool covering environment creation, resolution, locking and running
(`uv run`), and native `pyproject.toml` with no proprietary section.

## Decision

uv, exclusively. `backend/pyproject.toml` declares the project and `backend/uv.lock` is
committed. `uv add` / `uv sync` / `uv run` are the only supported commands. **There is no
`requirements.txt` anywhere in the repository**, and there is no pip fallback path.

CI runs `uv sync --locked` rather than a plain sync.

## Consequences

**A `pyproject.toml` edit that never regenerated the lock fails in CI, loudly**, instead of
resolving to something no developer has. That is the whole point of `--locked`, and it is
the one line of the workflow that makes the lock file meaningful rather than decorative.

**There is deliberately no pip fallback**, and that is a real cost: a contributor without uv
cannot install the project at all. A fallback would be a second dependency resolution that
nothing tests, which is the state this decision exists to leave.

**The image layout follows from it.** `backend/Dockerfile` installs the virtualenv at
`/opt/venv`, deliberately outside the `/app` working directory, because the dev compose
service bind-mounts the host's `backend/` over `/app` — a venv at `/app/.venv` would be
hidden by the mount, and on this machine it would be a Windows-built venv shadowing the
container's interpreter. The project itself is never installed; the source is imported from
the working directory, which is what makes the bind mount plus `--reload` work. The
frontend's `/node_modules` placement is the same rule, arrived at independently.

**Two environment traps are permanent on the development machine and are encoded rather
than remembered.**

- `uv run pytest` dies with `os error 4551`: an Application Control policy blocks the
  generated console-script shim. Every invocation is therefore
  `uv run python -m pytest`. It is written once in `scripts/test.sh` / `scripts/test.ps1`,
  asserted by `backend/tests/unit/test_repo_scripts.py`, and CI calls the script rather than
  re-spelling the command ([ADR-0007](./0007-ci-calls-the-repos-commands.md)). `uv run ruff`
  is unaffected.
- `uv` is not on `PATH` there, and a stale `VIRTUAL_ENV` pointing at the *old* repository's
  venv makes uv target the wrong environment. Both are handled in `scripts/_common.sh` and
  `scripts/_common.ps1`, which resolve uv and clear `VIRTUAL_ENV` before running anything.

**A test that compares two implementations cannot see a mistake they both make.** The
`.ps1`/`.sh` parity tests were green while *both* halves invoked `uv <tool>` instead of
`uv run <tool>`. Where a shape is load-bearing and shared, it is now pinned per language as
well as compared across the pair.
