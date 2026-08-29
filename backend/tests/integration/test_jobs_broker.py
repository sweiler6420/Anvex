"""Celery against the real compose ``redis`` — the tier eager mode cannot stand in for.

``task_always_eager`` runs a task by calling it. It never serialises a message, never opens
a connection, never involves a worker, and would keep passing if the broker URL were
nonsense — so on its own it proves the task body and nothing about the wiring ANV-21 exists
to build. These tests do the round trip: publish to Redis, let a real consumer pick the
message up, read the result back out of the result backend.

Skips cleanly with Docker stopped, like every other container tier (``CLAUDE.md`` §6).
"""

from __future__ import annotations

import base64
import json

import pytest
from celery import Celery
from celery.result import AsyncResult

from app.domain.ingest import CALL_SPACING_SECONDS
from app.jobs import ingest as jobs_ingest
from app.jobs.celery_app import BEAT_SCHEDULE, celery_app
from app.jobs.health import PING_TASK_NAME
from app.jobs.ingest import (
    DISPATCH_EXPIRY_MARGIN_SECONDS,
    INGEST_ALL_TASK_NAME,
    INGEST_SYMBOL_TASK_NAME,
)
from app.services.ingest import IngestTarget
from tests import broker

#: How long a test waits for a worker in a thread of this process to answer. Generous
#: enough not to flake on a loaded machine, short enough that a broken wiring fails fast.
RESULT_TIMEOUT_SECONDS = 30


# --------------------------------------------------------------------- publishing only


def test_the_broker_is_reachable_with_the_configured_transport(broker_app: Celery) -> None:
    """A connection, not a ping to a URL: it is ``kombu`` that has to be able to speak to it."""
    with broker_app.connection_for_write() as connection:
        connection.ensure_connection(max_retries=1)

        assert connection.connected


def test_a_published_task_lands_on_the_queue_as_json(broker_app: Celery) -> None:
    """Half the round trip, asserted on its own so a failure is diagnosable.

    If the worker later returns nothing, this test says whether the message was never
    published or never consumed — which are different bugs in different processes.
    """
    queue = broker_app.conf.task_default_queue
    result = broker_app.send_task(PING_TASK_NAME)

    client = broker.redis_client()
    try:
        messages = broker.queued_messages(client, queue)
    finally:
        client.close()

    assert len(messages) == 1
    envelope = json.loads(messages[0])
    assert envelope["headers"]["task"] == PING_TASK_NAME
    assert envelope["headers"]["id"] == result.id
    assert envelope["content-type"] == "application/json"


# ------------------------------------------------------------------ the full round trip


def test_ping_executes_through_a_real_worker(broker_worker: Celery) -> None:
    """The proof the ticket is about: producer → Redis → worker → bridge → result backend."""
    result = celery_app.tasks[PING_TASK_NAME].delay()

    payload = result.get(timeout=RESULT_TIMEOUT_SECONDS)

    assert payload["status"] == "ok"
    assert payload["task_id"] == result.id
    assert payload["worker"], "the worker did not stamp its node name on the result"


def test_the_result_survives_a_round_trip_through_the_backend(broker_worker: Celery) -> None:
    """Read the result back the way an operator would, from a bare id.

    ``AsyncResult(id)`` shares nothing with the object ``delay()`` returned, so this fails if
    the value only ever existed in this process.
    """
    sent = celery_app.tasks[PING_TASK_NAME].delay()
    sent.get(timeout=RESULT_TIMEOUT_SECONDS)

    fetched = AsyncResult(sent.id, app=celery_app)

    assert fetched.state == "SUCCESS"
    assert fetched.result["pid"] == sent.result["pid"]


def test_the_queue_is_empty_once_the_task_has_been_acknowledged(broker_worker: Celery) -> None:
    """``acks_late`` acknowledges on return, so a completed task leaves nothing behind."""
    queue = celery_app.conf.task_default_queue
    celery_app.tasks[PING_TASK_NAME].delay().get(timeout=RESULT_TIMEOUT_SECONDS)

    client = broker.redis_client()
    try:
        assert broker.queued_messages(client, queue) == []
    finally:
        client.close()


# ------------------------------------------------------------------------------ beat


def test_beat_produces_the_scheduled_task(broker_app: Celery) -> None:
    """Beat's own entry, published by hand with the options the schedule declares.

    Running the real ``beat`` service for five minutes is not a test; what is testable is
    that the schedule's task name and options actually produce a routable message — the two
    ways a schedule entry silently does nothing.
    """
    entry = BEAT_SCHEDULE["health-ping"]
    queue = broker_app.conf.task_default_queue

    broker_app.send_task(entry["task"], **entry["options"])

    client = broker.redis_client()
    try:
        messages = broker.queued_messages(client, queue)
    finally:
        client.close()

    assert len(messages) == 1
    envelope = json.loads(messages[0])
    assert envelope["headers"]["task"] == entry["task"]
    assert envelope["headers"]["expires"] is not None


# ------------------------------------------------------------------------------ ingest


def test_the_ingest_fan_out_is_routable(broker_app: Celery) -> None:
    """ANV-22's beat entry, published by hand with the options the schedule declares.

    The two ways a schedule entry silently does nothing are a task name nobody registered
    and options the broker rejects; both are visible here without waiting an hour for a tick.
    """
    entry = BEAT_SCHEDULE["ingest-intraday"]
    queue = broker_app.conf.task_default_queue

    broker_app.send_task(entry["task"], **entry["options"])

    client = broker.redis_client()
    try:
        messages = broker.queued_messages(client, queue)
    finally:
        client.close()

    assert len(messages) == 1
    envelope = json.loads(messages[0])
    assert envelope["headers"]["task"] == INGEST_ALL_TASK_NAME
    assert envelope["headers"]["expires"] is not None


def test_a_staggered_ingest_target_serialises_and_carries_its_eta(broker_app: Celery) -> None:
    """The fan-out's pacing is a ``countdown`` on a message, so it has to survive the wire.

    Both halves are asserted because both are load-bearing: the ``eta`` header *is* the
    pacing, and ``expires`` is what stops a target nobody consumed running an hour late.
    ``kwargs`` are two strings on purpose — ``task_serializer="json"`` means a ``Month``
    object would have failed here rather than quietly in a worker.
    """
    queue = broker_app.conf.task_default_queue
    target = IngestTarget(ticker="ANVX", month="2026-03")
    countdown = CALL_SPACING_SECONDS

    broker_app.send_task(
        INGEST_SYMBOL_TASK_NAME,
        kwargs=target.as_message(),
        countdown=countdown,
        expires=countdown + DISPATCH_EXPIRY_MARGIN_SECONDS,
    )

    client = broker.redis_client()
    try:
        messages = broker.queued_messages(client, queue)
    finally:
        client.close()

    assert len(messages) == 1
    envelope = json.loads(messages[0])
    headers = envelope["headers"]
    assert headers["task"] == INGEST_SYMBOL_TASK_NAME
    assert envelope["content-type"] == "application/json"
    assert headers["eta"] is not None, "the countdown did not become an ETA on the message"
    assert headers["expires"] > headers["eta"]
    assert json.loads(base64.b64decode(envelope["body"]))[1] == {
        "ticker": "ANVX",
        "month": "2026-03",
    }


def test_the_fan_out_executes_through_a_real_worker_and_dispatches(
    broker_worker: Celery, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Producer → Redis → worker → bridge → service → result backend, for ANV-22's own task.

    ``IngestService`` is stubbed because this test is about the wiring rather than about the
    ingest: what it proves is that ``ingest_all`` crosses the bridge inside a real consumer,
    publishes its fan-out with the pacing intact, and returns a JSON-serialisable summary.
    The worker runs in a thread of *this* process, which is what makes the patch visible to
    it — a forked worker would not see it, and ANV-21 documented that limit.
    """
    dispatched: list[dict[str, object]] = []

    class StubService:
        def __init__(self, *_: object, **__: object) -> None: ...

        async def plan(self, **_: object) -> tuple[IngestTarget, ...]:
            return (IngestTarget("ANVX", "2026-03"), IngestTarget("ANVX", "2026-02"))

    monkeypatch.setattr(jobs_ingest, "IngestService", StubService)
    monkeypatch.setattr(
        jobs_ingest.ingest_symbol, "apply_async", lambda **kw: dispatched.append(kw)
    )

    result = celery_app.tasks[INGEST_ALL_TASK_NAME].delay()
    summary = result.get(timeout=RESULT_TIMEOUT_SECONDS)

    assert summary == {"dispatched": 2, "spans_seconds": CALL_SPACING_SECONDS}
    assert [message["countdown"] for message in dispatched] == [0, CALL_SPACING_SECONDS]
