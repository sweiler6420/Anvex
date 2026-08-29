"""The running Docker Compose stack is healthy end to end.

This is the only test in the suite that requires Docker, so it is **opt-in**: nothing
here runs unless ``ANVEX_COMPOSE_TEST=1`` is set. The default ``uv run python -m pytest``
must stay runnable on a laptop with the daemon stopped, and a skipped test is honest
where a test that silently passes against nothing is not.

To run it::

    docker compose up -d db redis minio minio-init api
    $env:ANVEX_COMPOSE_TEST = "1"
    uv run python -m pytest tests/integration/test_compose_health.py

What it asserts, and why each one is worth its own test:

* ``/health`` answers 200 — the container the compose ``HEALTHCHECK`` polls is really
  serving. This is the liveness probe, so it must pass *without* touching Postgres.
* ``/health/ready`` answers 200 — the API, from **inside** the compose network, can
  reach Postgres by its service name. That is the wiring compose is responsible for and
  it is not observable from the host any other way.
* Postgres and Redis answer on their published host ports — the mappings developers and
  the ANV-6 test harness actually connect through.

Every connection detail is read from the environment with the ``.env.example`` values as
defaults, so overriding a host port in ``.env`` is enough to keep this working.
"""

from __future__ import annotations

import os

import httpx
import pytest

#: The whole module is opt-in. Compared against the exact string so a stray "false" or
#: "0" left in a shell profile cannot switch a Docker-dependent suite on by accident.
COMPOSE_TESTS_ENABLED = os.getenv("ANVEX_COMPOSE_TEST") == "1"

pytestmark = pytest.mark.skipif(
    not COMPOSE_TESTS_ENABLED,
    reason="requires the compose stack; set ANVEX_COMPOSE_TEST=1 after `docker compose up`",
)

#: How long to wait on a service that is up but busy. Deliberately short: this test is a
#: smoke check against an already-converged stack, not a wait-for-startup loop — that is
#: what the compose healthchecks and `depends_on` conditions are for.
TIMEOUT_SECONDS = 10.0


def _api_base_url() -> str:
    return os.getenv("ANVEX_COMPOSE_API_URL", "http://localhost:8000").rstrip("/")


def _published_port(env_var: str, default: int) -> int:
    return int(os.getenv(env_var, str(default)))


async def test_liveness_probe_answers_ok() -> None:
    """`GET /health` is 200 and matches the `HealthOut` contract."""
    async with httpx.AsyncClient(base_url=_api_base_url(), timeout=TIMEOUT_SECONDS) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_probe_reaches_postgres_from_the_container() -> None:
    """`GET /health/ready` is 200, proving the API resolved and queried the `db` service.

    A 503 here means the API is alive but `POSTGRES_HOST`/`POSTGRES_PORT` inside the
    container do not point at a reachable database — the single most likely compose
    misconfiguration, and one the liveness probe is designed *not* to notice.
    """
    async with httpx.AsyncClient(base_url=_api_base_url(), timeout=TIMEOUT_SECONDS) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_request_id_header_is_present() -> None:
    """The middleware stack really is installed in the container, not just in tests."""
    async with httpx.AsyncClient(base_url=_api_base_url(), timeout=TIMEOUT_SECONDS) as client:
        response = await client.get("/health")

    assert response.headers.get("X-Request-ID")


async def test_postgres_is_reachable_on_its_published_port() -> None:
    """The `db` service answers on the host port the mapping publishes."""
    import asyncpg

    from app.settings import get_settings

    settings = get_settings()
    connection = await asyncpg.connect(
        host="localhost",
        port=_published_port("POSTGRES_HOST_PORT", 5442),
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        database=settings.postgres_db,
        timeout=TIMEOUT_SECONDS,
    )
    try:
        assert await connection.fetchval("SELECT 1") == 1
    finally:
        await connection.close()


async def test_redis_is_reachable_on_its_published_port() -> None:
    """The `redis` service answers PING on the host port the mapping publishes.

    Imported lazily because `redis` arrives as a transitive dependency of `celery[redis]`
    rather than a direct one; keeping the import inside the test means collection of the
    default (skipped) suite never depends on it.
    """
    from redis.asyncio import Redis

    port = _published_port("REDIS_HOST_PORT", 6379)
    redis = Redis(host="localhost", port=port, socket_timeout=TIMEOUT_SECONDS)
    try:
        assert await redis.ping() is True
    finally:
        await redis.aclose()
