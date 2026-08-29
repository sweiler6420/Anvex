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
| `scripts/`  | Repo-wide developer scripts                                             |
| `.env`      | Single environment file for every stack (copy from `.env.example`)      |

## Quick start

```bash
cp .env.example .env      # then fill in API keys
docker compose up --build
```

- API: http://localhost:8000 (docs at `/docs`)
- Web: http://localhost:5173

## Development

Backend dependencies are managed with [uv](https://docs.astral.sh/uv/):

```bash
cd backend
uv sync
uv run pytest
```

Frontend:

```bash
cd frontend
npm install
npm run dev
npm run test
```

## Architecture

See [`CLAUDE.md`](./CLAUDE.md) for the layering contract — where each kind of code belongs and
why. It is the authoritative description of the codebase's structure.
