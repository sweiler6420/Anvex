"""The ``ping`` heartbeat — the one task with no service behind it.

Every other task in ``app/jobs/`` is a thin entry point onto a service (``CLAUDE.md`` §3).
This one deliberately has nothing behind it, because its job is to answer a question about
the *plumbing* rather than about Anvex: did a message travel from a producer through Redis
to a worker, get deserialised as JSON, cross the sync/async bridge, and come back as a
result? A task with business logic cannot answer that — a failure would be ambiguous between
the wiring and the work.

It touches no database and no vendor on purpose. A heartbeat that needs Postgres reports
"Postgres is down" as "the worker is down", and the two are worth telling apart.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Final

import structlog

from app.jobs.base import AnvexTask, run_async
from app.jobs.celery_app import celery_app
from app.settings import get_settings

logger = structlog.get_logger("anvex.jobs.health")

#: Explicit, so a beat entry and a queued message keep naming the same task if this module
#: is ever renamed. See ``app/jobs/celery_app.py``.
PING_TASK_NAME: Final = "jobs.health.ping"


async def _ping(*, task_id: str | None, worker: str | None) -> dict[str, Any]:
    """The async half. Describes the process that ran it and nothing else.

    :func:`asyncio.get_running_loop` is not decoration: it is what makes "this ran through
    the bridge" a fact the task can fail on rather than a claim in a docstring. Calling this
    coroutine outside a loop raises.
    """
    asyncio.get_running_loop()
    payload: dict[str, Any] = {
        "status": "ok",
        "env": get_settings().anvex_env,
        "pid": os.getpid(),
        "task_id": task_id,
        "worker": worker,
    }
    logger.info("jobs.health.ping", **payload)
    return payload


@celery_app.task(name=PING_TASK_NAME, bind=True)
def ping(self: AnvexTask) -> dict[str, Any]:
    """Return a small JSON-serialisable description of the worker that ran this.

    The return value is what proves the *result backend* too: an operator can read it back
    with ``AsyncResult(task_id).get()``, and it names the pid, so two workers are
    distinguishable.
    """
    return run_async(lambda: _ping(task_id=self.request.id, worker=self.request.hostname))


__all__ = ["PING_TASK_NAME", "ping"]
