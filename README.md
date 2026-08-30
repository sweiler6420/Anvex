# Anvex

Investment research platform — research clearer, invest sharper.

A monorepo containing an async **FastAPI + Celery** backend and a **Vite + React** frontend,
backed by **Postgres** and **S3**. Runs entirely locally through Docker Compose; AWS is the
eventual deployment target.

## Layout

| Path        | What lives here                                                        |
| ----------- | ---------------------------------------------------------------------- |
| `backend/`  | Async FastAPI API, Celery workers, Alembic migrations, pytest suite     |
| `frontend/` | Vite + React app with TanStack Router and JWT auth                      |
| `docs/`     | Cross-stack documentation and architecture decision records             |
| `scripts/`  | Repo-wide developer scripts, in PowerShell and sh                       |
| `.env`      | Single environment file for every stack (copy from `.env.example`)      |

## Quick start

```powershell
Copy-Item .env.example .env      # then fill in API keys
.\scripts\up.ps1                 # db, redis, minio, minio-init, api
.\scripts\migrate.ps1
.\scripts\seed.ps1
```

```sh
cp .env.example .env             # then fill in API keys
./scripts/up.sh                  # db, redis, minio, minio-init, api
./scripts/migrate.sh
./scripts/seed.sh
```

- API: http://localhost:8000 (docs at `/docs`)
- Web: http://localhost:5173, once `up frontend` has started it

## Scripts

Every command exists twice, once per shell, and the two halves are kept identical —
`backend/tests/unit/test_repo_scripts.py` compares them and fails on drift. **Both take the
same command line**: the PowerShell halves parse `--flag` by hand rather than declaring
`[switch]` parameters, so every example below works verbatim in either shell. Run them from
anywhere; they resolve the repo root themselves.

| Command | What it does |
| --- | --- |
| `up` | start services: `core` (default), `celery`, `frontend`, `db-test` or `all` |
| `down` | stop everything; `--volumes` also deletes the named volumes (asks first) |
| `logs` | follow container output, optionally for named services |
| `migrate` | `alembic upgrade`, to `head` by default |
| `makemigration` | `alembic revision --autogenerate -m "<message>"` |
| `seed` | load the checked-in seed data — wraps `backend/scripts/seed_politicians.py` |
| `test` | `backend` (pytest), `frontend` (vitest in the container), or both (default) |
| `lint` | `ruff check` + `ruff format --check`, and eslint; `backend`/`frontend`/`all` |
| `fmt` | `ruff format` + `ruff check --fix`, backend only; `--check` writes nothing |
| `reset-db` | delete the dev database volume, recreate, migrate and seed (asks first) |

```powershell
.\scripts\up.ps1 frontend
.\scripts\test.ps1 backend -k watchlist
.\scripts\lint.ps1
.\scripts\reset-db.ps1 --yes
```

```sh
./scripts/up.sh frontend
./scripts/test.sh backend -k watchlist
./scripts/lint.sh
./scripts/reset-db.sh --yes
```

Each script's header comment is its full documentation — the `Usage:` line, the flags, and
the reasoning behind anything surprising. Three of those reasons are load-bearing and must
survive future edits:

- **pytest is always `uv run python -m pytest`, never `uv run pytest`.** An Application
  Control policy on the dev machine blocks the generated console-script shim, and the run
  dies with `os error 4551`.
- **Every frontend command runs in the `web` container via `docker compose exec -T`.**
  There is no node on the dev host, and without `-T` the command hangs in any
  non-interactive shell.
- **Nothing sets `NODE_ENV`.** Vite honours an inherited one over its own mode, so
  `NODE_ENV=development` silently ships a 330 kB development bundle.

`migrate`, `makemigration`, `seed` and `reset-db` reach the database from the *host*, where
the `POSTGRES_HOST=db` in `.env` — a compose service name — does not resolve. They translate
it to `localhost` and the port compose publishes, both read from that same `.env`, so there
is still one place to change it. Set `POSTGRES_HOST` yourself and they leave it alone.

`backend/scripts/` keeps the backend-only entry points (`seed_politicians.py`); `scripts/`
only ever wraps them, so there is one implementation of each behaviour.

## Continuous integration

[`.github/workflows/ci.yml`](./.github/workflows/ci.yml) runs on every push to `main` and on
every pull request. Two jobs, gated on a path filter so each stack only builds when it
changes:

| Job | What it runs | Services |
| --- | --- | --- |
| **Backend** | `uv sync --locked`, then `scripts/lint.sh backend` (`ruff check` + `ruff format --check`) and `scripts/test.sh backend` with coverage | Postgres 16 and Redis 7 |
| **Frontend** | `npm ci`, `npm run lint`, `npm run test`, `npm run build` | none |

**The workflow calls the scripts rather than repeating them.** Every trap the backend
commands carry — `python -m pytest` and never the console script, the cleared `VIRTUAL_ENV`
— is encoded once, in `scripts/`, and `backend/tests/unit/test_repo_scripts.py` is what keeps
the two halves of that directory honest. The frontend job calls the `package.json` scripts
directly, because the only thing `scripts/lint.sh frontend` adds is
`docker compose exec -T web`, and that exists solely because the dev host has no node.

Four things in there are deliberate and are asserted by
`backend/tests/unit/test_ci_workflow.py`:

- **The backend filter reaches into the frontend.** It matches
  `frontend/src/features/auth/components/SignUpPage.jsx`, because
  `tests/unit/test_domain_password.py` parses that file to keep the client and server
  password policies from drifting. A filter of `backend/**` alone would skip the backend job
  on exactly the commit that guard exists for. `README.md`, `CLAUDE.md`, `.env.example`,
  `docker-compose.yml` and `scripts/` are in it for the same reason.
- **Both jobs check out the whole repository.** Those tests *fail* rather than skip when a
  file is missing, and Vite's `envDir` points one level above `frontend/`.
- **The service tiers are asserted reachable** before the suite runs. Every tier skips
  politely when its service is absent, so a mistyped port would otherwise produce a green
  run over a suite that tested nothing. MinIO is *not* a service container — its image needs
  `server /data` as an argument and a service container cannot pass one — so the S3 tier
  skips in CI and says so in the log.
- **Nothing sets `NODE_ENV`,** and the build is checked for `jsxDEV` afterwards. Vite honours
  an inherited `NODE_ENV` over its own mode, so `NODE_ENV=development` ships a development
  bundle with no warning and exit code 0.

## Development

Backend dependencies are managed with [uv](https://docs.astral.sh/uv/); run from `backend/`:

```sh
uv sync
uv run python -m pytest
```

The frontend has no host toolchain at all — **node is deliberately not installed** — so
every npm, vite, vitest and eslint command runs inside the `web` container. See
[`frontend/README.md`](./frontend/README.md) for the one-shot `docker run` form, and
[`backend/docs/testing.md`](./backend/docs/testing.md) for the test tiers and what each one
needs running.

## Architecture

See [`CLAUDE.md`](./CLAUDE.md) for the layering contract — where each kind of code belongs and
why. It is the authoritative description of the codebase's structure.
