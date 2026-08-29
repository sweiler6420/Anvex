"""The Celery application: broker wiring, execution policy and the beat schedule.

``celery -A app.jobs.celery_app worker`` and ``… beat`` both import this module and find
:data:`celery_app`. :func:`create_celery_app` exists for the same reason
``app.main.create_app`` does — so a test can build an instance against different
:class:`~app.settings.Settings` instead of monkeypatching a global.

Everything configurable comes from ``app/settings.py`` (``CLAUDE.md`` §4: nothing else reads
the environment). Everything *policy* — serialisation, acknowledgement, time limits — is a
constant here, because it is a decision about how Anvex runs jobs rather than a knob a
deployment turns.

At-least-once, and the two failures that decide it
--------------------------------------------------

``task_acks_late = True``: the broker's message is acknowledged when the task **returns**,
not when it is handed to a worker. A worker that loses its Redis connection, or is shut down
gracefully mid-task, therefore leaves the message on the queue and it is delivered again.
The cost is that a task can run twice, which is exactly why ``CLAUDE.md`` §3 requires tasks
to be idempotent — that requirement and this setting are one decision, not two.

``task_reject_on_worker_lost = False``, stated explicitly rather than inherited: when a
worker *process* dies mid-task (SIGKILL, OOM, a segfault in a C extension), the message is
acknowledged and the task is recorded as failed, rather than being redelivered. This is the
deliberately asymmetric half of the pair, and the asymmetry is the point:

* A lost **connection** is a property of the network, so redelivering is free of risk — the
  next attempt is on a healthy worker.
* A lost **process** is very often a property of the *message*: the task that OOMed will OOM
  again, and redelivering it means it kills every worker that picks it up, forever. That
  poison-message loop has no natural end, and it takes the whole worker pool with it.

So the trade accepted here is: **lose one run, keep the workers.** A job whose loss actually
matters is re-driven by beat on its next tick — which is safe precisely because it is
idempotent — rather than by broker redelivery. A job for which that is not good enough
should carry its own durable "did this window complete" record; it should not flip this flag.

``worker_prefetch_multiplier = 1`` follows from ``acks_late``: with a larger prefetch a
worker holds several unacknowledged long-running messages, so a restart redelivers all of
them and a slow task blocks the ones queued behind it on the same worker.

**The broker's visibility timeout must exceed the hard time limit.** Redis has no real
acknowledgement; ``kombu`` re-delivers any message whose worker has not finished within
``visibility_timeout``. If that were shorter than :data:`TASK_TIME_LIMIT_SECONDS`, a slow
task would be handed to a *second* worker while the first was still running it — concurrent
duplicate execution, which no amount of idempotency makes free. The two constants are
ordered here and a test asserts the ordering, because the failure is invisible until a job
gets slow.

Names are explicit
------------------

Every task passes ``name="jobs.<module>.<function>"``. Celery's default is the dotted import
path, so renaming or moving a module silently orphans every message already on the queue and
every beat entry naming it. An explicit name makes the task's identity a decision rather
than a side effect of the file layout.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import structlog
from celery import Celery
from celery.signals import setup_logging, worker_process_init

from app.db.engine import reset_engine
from app.jobs.base import AnvexTask
from app.middleware import configure_logging
from app.settings import Settings, get_settings

logger = structlog.get_logger("anvex.jobs")

#: The Celery app's ``main`` name — the prefix Celery would use for unnamed tasks and the
#: default node-name prefix a worker reports as. Ours are all named explicitly.
CELERY_MAIN = "anvex"

#: Modules imported at worker/beat boot so their ``@celery_app.task`` decorators run. A task
#: that is not in a module listed here is unroutable: the worker answers
#: ``NotRegistered``. A unit test derives this list from the contents of ``app/jobs/`` and
#: fails when a new module is added without being registered.
TASK_MODULES: tuple[str, ...] = ("app.jobs.health",)

#: Seconds a task may run before ``SoftTimeLimitExceeded`` is raised *inside* it — which is
#: recoverable: the exception unwinds through the bridge's ``finally``, so the engine is
#: still disposed and the session still closed.
TASK_SOFT_TIME_LIMIT_SECONDS = 900
#: Seconds before the worker kills the child outright. The gap between the two is the budget
#: a task has to clean up after the soft limit fires.
TASK_TIME_LIMIT_SECONDS = 960
#: Must stay **greater** than :data:`TASK_TIME_LIMIT_SECONDS` — see the module docstring.
BROKER_VISIBILITY_TIMEOUT_SECONDS = 3600

#: How long a task's return value stays in the result backend. Long enough to debug a
#: failure the morning after; short enough that Redis is not an archive.
RESULT_EXPIRES = timedelta(hours=24)

#: Recycle a prefork child after this many tasks. Cheap insurance against a slow leak in a
#: dependency, and it bounds the lifetime of anything a task leaves behind in the process.
WORKER_MAX_TASKS_PER_CHILD = 200

#: How often the heartbeat runs. Frequent enough that "is the worker consuming?" is
#: answerable from the log without waiting, rare enough to be noise-free.
PING_INTERVAL = timedelta(minutes=5)

#: The beat schedule. One entry per scheduled job::
#:
#:     "<slug>": {"task": <registered name>, "schedule": <timedelta|crontab>,
#:                "options": {"expires": <seconds>}}
#:
#: ``expires`` is **not** optional decoration. Beat keeps publishing while the workers are
#: down, so a queue that is not being consumed accumulates one message per tick; without an
#: expiry, bringing the workers back replays hours of stale ticks at once. An expiry shorter
#: than the interval means at most one pending run of any job — the next tick is the retry.
BEAT_SCHEDULE: dict[str, dict[str, Any]] = {
    "health-ping": {
        "task": "jobs.health.ping",
        "schedule": PING_INTERVAL,
        "options": {"expires": int(PING_INTERVAL.total_seconds()) - 60},
    },
}


def create_celery_app(settings: Settings | None = None) -> Celery:
    """Build a configured Celery application.

    ``app = create_celery_app()`` at the bottom of this module is what the worker, beat and
    every task decorator use. Passing explicit ``settings`` is for tests.
    """
    settings = settings or get_settings()

    app = Celery(CELERY_MAIN, task_cls=AnvexTask)
    app.conf.update(
        # ----- transport -----
        broker_url=settings.celery_broker_url,
        result_backend=settings.celery_result_backend,
        # Redis is usually a second or two behind the worker in the compose graph, and in
        # AWS an ElastiCache failover is a reconnect rather than a crash.
        broker_connection_retry_on_startup=True,
        broker_transport_options={"visibility_timeout": BROKER_VISIBILITY_TIMEOUT_SECONDS},
        result_backend_transport_options={"visibility_timeout": BROKER_VISIBILITY_TIMEOUT_SECONDS},
        result_expires=RESULT_EXPIRES,
        # ----- serialisation -----
        # JSON only, in both directions. `pickle` would execute arbitrary code from
        # whatever can write to Redis, and it makes the queue a Python-only interface.
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        result_accept_content=["json"],
        # ----- time -----
        timezone="UTC",
        enable_utc=True,
        # ----- discovery -----
        imports=TASK_MODULES,
        beat_schedule=BEAT_SCHEDULE,
        # ----- execution policy (see the module docstring) -----
        task_acks_late=True,
        task_reject_on_worker_lost=False,
        worker_prefetch_multiplier=1,
        task_track_started=True,
        task_soft_time_limit=TASK_SOFT_TIME_LIMIT_SECONDS,
        task_time_limit=TASK_TIME_LIMIT_SECONDS,
        worker_max_tasks_per_child=WORKER_MAX_TASKS_PER_CHILD,
        # A task that fails is a failure, not a `None` result the caller has to interpret.
        task_remote_tracebacks=False,
        # ----- logging -----
        # `configure_logging` owns the root logger (`app/middleware/logging.py`); letting
        # Celery hijack it would give the worker a second, differently-formatted log.
        worker_hijack_root_logger=False,
        worker_redirect_stdouts=False,
    )
    return app


celery_app = create_celery_app()


@setup_logging.connect
def _configure_worker_logging(**_: Any) -> None:
    """Give the worker the API's structlog configuration.

    Connecting to ``setup_logging`` at all is what tells Celery to leave logging alone, so
    worker lines come out in the same shape as request lines and a log shipper needs one
    parser rather than two.
    """
    configure_logging(get_settings())


@worker_process_init.connect
def _reset_engine_after_fork(**_: Any) -> None:
    """Discard any inherited database engine in a freshly forked child.

    ``worker_process_init`` fires **in the child**, after the fork. Nothing in the worker's
    boot path builds an engine (the engine is lazy and the parent runs no tasks), so this is
    normally a no-op — and it is here precisely because "normally" is not a guarantee: the
    day something touches the database from a ``worker_init`` hook, the alternative to this
    line is two processes quietly sharing one Postgres socket.
    """
    reset_engine()


__all__ = [
    "BEAT_SCHEDULE",
    "BROKER_VISIBILITY_TIMEOUT_SECONDS",
    "CELERY_MAIN",
    "PING_INTERVAL",
    "RESULT_EXPIRES",
    "TASK_MODULES",
    "TASK_SOFT_TIME_LIMIT_SECONDS",
    "TASK_TIME_LIMIT_SECONDS",
    "WORKER_MAX_TASKS_PER_CHILD",
    "celery_app",
    "create_celery_app",
]
