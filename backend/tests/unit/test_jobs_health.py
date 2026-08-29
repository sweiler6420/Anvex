"""Unit tests for the ``ping`` heartbeat and for eager-mode execution.

Eager mode runs a task in-process with no broker at all, which makes it the right tool for
"does the task body do what it says" and the wrong tool for "is the wiring correct" — it
never serialises a message and never involves a worker. The round trip through real Redis is
``tests/integration/test_jobs_broker.py``; both exist because either alone is misleading.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator

import pytest

from app.db import engine as db_engine
from app.jobs.celery_app import celery_app
from app.jobs.health import PING_TASK_NAME, _ping, ping
from app.settings import get_settings


@pytest.fixture
def eager() -> Iterator[None]:
    """Run tasks in-process. Restored afterwards so no other test inherits it."""
    previous = (celery_app.conf.task_always_eager, celery_app.conf.task_eager_propagates)
    celery_app.conf.update(task_always_eager=True, task_eager_propagates=False)
    try:
        yield
    finally:
        celery_app.conf.update(task_always_eager=previous[0], task_eager_propagates=previous[1])


# --------------------------------------------------------------------------- the async half


async def test_the_async_half_describes_this_process() -> None:
    payload = await _ping(task_id="abc", worker="celery@host")

    assert payload == {
        "status": "ok",
        "env": get_settings().anvex_env,
        "pid": os.getpid(),
        "task_id": "abc",
        "worker": "celery@host",
    }


def test_the_async_half_cannot_run_without_a_loop() -> None:
    """The ``get_running_loop()`` call is the guard that keeps the bridge from being skipped.

    Driving the coroutine by hand — no loop anywhere — reaches that first line and stops
    there, which is the point: a future "optimisation" that called ``_ping`` synchronously
    would fail here rather than quietly work until something in it actually awaited.
    """
    coro = _ping(task_id=None, worker=None)
    try:
        with pytest.raises(RuntimeError, match="no running event loop"):
            coro.send(None)
    finally:
        coro.close()


# ------------------------------------------------------------------------------ the task


def test_the_task_is_registered_under_its_explicit_name() -> None:
    assert ping.name == PING_TASK_NAME
    assert PING_TASK_NAME in celery_app.tasks
    assert celery_app.tasks[PING_TASK_NAME].name == PING_TASK_NAME


def test_eager_execution_returns_the_payload(eager: None) -> None:
    result = ping.delay()

    assert result.successful()
    assert result.get()["status"] == "ok"
    assert result.get()["pid"] == os.getpid()


def test_eager_execution_carries_the_task_id(eager: None) -> None:
    result = ping.delay()

    assert result.get()["task_id"] == result.id


def test_the_result_is_json_serialisable() -> None:
    """``task_serializer="json"`` is only a promise until something has to keep it.

    A task returning a ``Decimal`` or a ``datetime`` fails at the result backend, in the
    worker, long after the code that produced it looked fine.
    """
    payload = ping.apply().get()

    assert json.loads(json.dumps(payload)) == payload


def test_the_heartbeat_never_reaches_for_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ping that needs Postgres reports "Postgres is down" as "the worker is down".

    Those are worth telling apart, so the heartbeat opens no engine at all — asserted rather
    than intended, because the cheapest way to make a health task "more useful" is to have it
    ``SELECT 1``.
    """
    monkeypatch.setattr(db_engine, "_engine", None)

    payload = ping.apply().get()

    assert payload["status"] == "ok"
    assert db_engine.current_engine() is None
