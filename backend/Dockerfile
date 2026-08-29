# syntax=docker/dockerfile:1.7
#
# Anvex backend image. Build context is `backend/` (see the root docker-compose.yml).
#
# Two stages:
#   builder  — resolves and installs dependencies with uv from the committed uv.lock.
#   runtime  — copies only the finished virtualenv plus the application source.
#
# Design notes worth keeping:
#
#  * The virtualenv lives at **/opt/venv, not /app/.venv**. The dev compose service
#    bind-mounts ./backend over /app; a venv inside /app would be hidden by that mount
#    (and the host's Windows-built .venv would shadow it), so the interpreter must live
#    outside the mount point. `UV_PROJECT_ENVIRONMENT` tells uv where to put it.
#  * Dependencies are installed from pyproject.toml + uv.lock *before* the source is
#    copied, so editing application code re-uses the (slow) dependency layer.
#  * `--frozen` fails the build if uv.lock is out of date with pyproject.toml rather than
#    silently re-resolving. A container build is exactly where a drifting lock must hurt.
#  * `--no-install-project`: the project itself is never pip-installed. The source is
#    copied to /app and imported from the working directory, which is what makes the dev
#    bind mount + `--reload` work without a reinstall.
#  * `--no-dev`: pytest/ruff/respx stay out of the runtime image. The test suite runs on
#    the host (and, from ANV-6, against the compose `db-test` service).

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.11.0


# --------------------------------------------------------------------------- builder
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

COPY --from=uv /uv /uvx /usr/local/bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /src

# Dependency layer: only the two files that can change the resolution.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project


# --------------------------------------------------------------------------- runtime
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}" \
    VIRTUAL_ENV=/opt/venv \
    APP_PORT=8000

# Non-root from here on. uid/gid 1000 matches the usual desktop user, which keeps
# bind-mounted source readable on Linux hosts as well as Docker Desktop.
RUN groupadd --gid 1000 anvex \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin anvex

COPY --from=builder --chown=anvex:anvex /opt/venv /opt/venv

WORKDIR /app
COPY --chown=anvex:anvex alembic.ini ./alembic.ini
COPY --chown=anvex:anvex app ./app

USER anvex

EXPOSE 8000

# Liveness only — deliberately `/health`, never `/health/ready`.
#
# Readiness runs `SELECT 1` against Postgres. Wiring that to the container healthcheck
# would make Docker kill and restart the API every time the database blips, turning a
# transient DB hiccup into an API restart loop. `/health/ready` is what `depends_on`
# gates and what a load balancer target group polls; `/health` is what keeps the
# container alive. Uses stdlib urllib so the image needs no curl.
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=5 \
    CMD ["python", "-c", "import os,sys,urllib.request; url='http://127.0.0.1:'+os.environ.get('APP_PORT','8000')+'/health'; sys.exit(0 if urllib.request.urlopen(url, timeout=4).status == 200 else 1)"]

# Startup opens no socket (the engine is lazy, ANV-3), so the API boots even while
# Postgres is still starting. Compose overrides this with --reload for development.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
