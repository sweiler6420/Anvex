"""How the test suite reaches the compose ``redis`` container.

The third container tier, written to the shape :mod:`tests.database` set and
:mod:`tests.storage` copied (``CLAUDE.md`` §6): a test-only ``BaseSettings`` on the same
repo-root ``.env``, a ``@cache``d :func:`unavailable_reason` where **every** failure is a
skip, and helpers that hand each test its own namespace.

**Its own Redis databases, not the application's.** ``db-test`` is a second Postgres and the
throwaway bucket is a second bucket; the equivalent here is a second *logical database* on
the same Redis, because Redis numbers sixteen of them and a compose stack does not need a
second container to get isolation. The suite uses 14 and 15 rather than the app's 0 and 1,
so a developer's running ``worker`` cannot consume a message the tests published — which it
otherwise would, silently, and the test would hang waiting for a result it never gets.

**The URL is built here, not in ``app/settings.py``.** ``CELERY_BROKER_URL`` points at the
in-network ``redis://redis:6379/0`` because the app always runs in a container; pytest
normally runs on the host and has to dial the published ``localhost`` port. Same reasoning,
and the same "one number in ``.env`` moves both the compose mapping and the client" rule:
``REDIS_HOST_PORT`` is what is read.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from functools import cache

import redis
from celery import Celery
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.jobs.celery_app import celery_app
from app.settings import ENV_FILE

#: Seconds to wait for Redis to answer. Short on purpose: with the container stopped this is
#: dead time on every developer's fast run.
PROBE_TIMEOUT_SECONDS: float = 3.0

#: Logical Redis databases reserved for the suite. Deliberately the far end of the range, so
#: they cannot collide with the app's broker (0) or result backend (1).
BROKER_DB: int = 14
RESULT_DB: int = 15


class HarnessBrokerSettings(BaseSettings):
    """Test-only view of how to reach Redis from the host.

    Not named ``Test…`` — pytest would try to collect it as a test class.
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    #: The published host port from ``.env``.
    redis_host_port: int = 6379
    #: Overrides the whole broker URL, for the in-network case
    #: (``redis://redis:6379/14``) or a Redis somewhere else.
    celery_test_broker_url: str | None = None
    #: Same, for the result backend.
    celery_test_result_backend: str | None = None


@cache
def harness_settings() -> HarnessBrokerSettings:
    """The harness's broker settings, read once per process."""
    return HarnessBrokerSettings()


def broker_url() -> str:
    """Where the test suite publishes tasks."""
    harness = harness_settings()
    return (
        harness.celery_test_broker_url or f"redis://localhost:{harness.redis_host_port}/{BROKER_DB}"
    )


def result_backend_url() -> str:
    """Where the test suite reads task results from."""
    harness = harness_settings()
    return (
        harness.celery_test_result_backend
        or f"redis://localhost:{harness.redis_host_port}/{RESULT_DB}"
    )


def describe_target() -> str:
    """Human-readable broker URL, for skip messages."""
    return broker_url()


@cache
def unavailable_reason() -> str | None:
    """``None`` when Redis answers, otherwise the reason to skip.

    Cached: one probe per session keeps the "Docker is stopped" path cheap, and the answer
    cannot change usefully mid-run.
    """
    try:
        client = redis.Redis.from_url(
            broker_url(),
            socket_connect_timeout=PROBE_TIMEOUT_SECONDS,
            socket_timeout=PROBE_TIMEOUT_SECONDS,
        )
        try:
            client.ping()
        finally:
            client.close()
    except Exception as exc:  # every failure to reach it is equally a skip
        return (
            f"no Redis at {describe_target()} ({type(exc).__name__}: {exc}). "
            "Start it with `docker compose up -d redis`."
        )
    return None


def flush() -> None:
    """Empty both test databases.

    The broker tier's analogue of the rolled-back transaction: there is no Redis
    transaction to roll back, so isolation is "start from empty and leave it empty".
    """
    for url in (broker_url(), result_backend_url()):
        client = redis.Redis.from_url(url, socket_connect_timeout=PROBE_TIMEOUT_SECONDS)
        try:
            client.flushdb()
        finally:
            client.close()


def queued_messages(client: redis.Redis, queue: str) -> list[bytes]:
    """Every raw message currently sitting on ``queue``.

    ``kombu`` stores a Redis-transport queue as a plain list of JSON envelopes, which is
    what lets a test assert that a message was *published* without needing a worker to
    consume it — the two halves of the round trip are then separately diagnosable.
    """
    return list(client.lrange(queue, 0, -1))


def redis_client(url: str | None = None) -> redis.Redis:
    """A plain ``redis-py`` client on the test broker, for inspecting raw state."""
    return redis.Redis.from_url(url or broker_url(), socket_connect_timeout=PROBE_TIMEOUT_SECONDS)


@contextmanager
def use_test_broker() -> Iterator[Celery]:
    """Point the real :data:`~app.jobs.celery_app.celery_app` at the test Redis.

    The application Celery app is repointed rather than replaced, because Celery binds a
    task to the app whose decorator created it: a second :class:`~celery.Celery` built by
    :func:`~app.jobs.celery_app.create_celery_app` would have an empty registry and could
    only ``send_task`` by name, so the thing under test would no longer be the thing that
    runs in production. The two URLs are restored afterwards and the connection pools are
    closed, so nothing leaks into the rest of the session.
    """
    previous_broker = celery_app.conf.broker_url
    previous_backend = celery_app.conf.result_backend
    celery_app.conf.update(broker_url=broker_url(), result_backend=result_backend_url())
    flush()
    try:
        yield celery_app
    finally:
        with suppress(Exception):
            flush()
        with suppress(Exception):
            celery_app.close()
        celery_app.conf.update(broker_url=previous_broker, result_backend=previous_backend)


__all__ = [
    "BROKER_DB",
    "PROBE_TIMEOUT_SECONDS",
    "RESULT_DB",
    "HarnessBrokerSettings",
    "broker_url",
    "describe_target",
    "flush",
    "harness_settings",
    "queued_messages",
    "redis_client",
    "result_backend_url",
    "unavailable_reason",
    "use_test_broker",
]
