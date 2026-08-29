"""Celery against the real compose ``redis`` — the tier eager mode cannot stand in for.

``task_always_eager`` runs a task by calling it. It never serialises a message, never opens
a connection, never involves a worker, and would keep passing if the broker URL were
nonsense — so on its own it proves the task body and nothing about the wiring ANV-21 exists
to build. These tests do the round trip: publish to Redis, let a real consumer pick the
message up, read the result back out of the result backend.

Skips cleanly with Docker stopped, like every other container tier (``CLAUDE.md`` §6).
"""

from __future__ import annotations

import json

from celery import Celery
from celery.result import AsyncResult

from app.jobs.celery_app import BEAT_SCHEDULE, celery_app
from app.jobs.health import PING_TASK_NAME
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
