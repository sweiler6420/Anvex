"""The sync/async bridge every Celery task crosses, and the base task class.

``CLAUDE.md`` §2 says the backend is async everywhere. Celery is not: a task's ``run`` is
called synchronously on a worker thread or a forked process. Exactly one module is allowed
to reconcile those two facts, and this is it — a task that invents its own bridge is how
this layer rots (ANV-21).

The bridge is :func:`run_async`
------------------------------

A task body is one line::

    @celery_app.task(name="jobs.news.sync_symbol", bind=True)
    def sync_symbol(self: AnvexTask, symbol: str) -> int:
        return run_async(lambda: _sync_symbol(symbol))

    async def _sync_symbol(symbol: str) -> int:
        settings = get_settings()
        async with get_session() as session:
            return await NewsService(session, settings).sync_for_symbol(symbol=symbol)

The sync half is the Celery entry point and does nothing but call the bridge. The async
half resolves its dependencies and calls **one** service — the same shape as an API
handler (``CLAUDE.md`` §3), for the same reason. Business logic never appears in either.

**It takes a factory, not a coroutine.** ``run_async(_sync_symbol(symbol))`` would build the
coroutine at the call site, outside the loop that is about to be created, and — if the
bridge ever declined to run it — leave a "coroutine was never awaited" warning instead of an
error. A zero-argument callable makes "nothing async is constructed before the loop exists"
a rule the signature enforces; passing a coroutine object is a :class:`TypeError` with the
fix in the message.

One loop per task, and what that costs
--------------------------------------

:func:`run_async` calls :func:`asyncio.run`, so **each task creates and closes its own event
loop**, and closes the database engine inside that loop on the way out. The cost is real and
is accepted rather than overlooked: a task gets **no cross-task connection pooling**, so
every task pays one Postgres connect (and one TLS handshake, in AWS) instead of borrowing a
warm connection. Three reasons that is the right trade here:

* **A task is a batch, not a request.** It opens one session and does many queries in it, so
  the handshake is amortised over the whole job rather than over one query. The API, where
  the ratio is the other way round, keeps its pool.
* **The alternative is a long-lived loop, and it is a much bigger object.** Keeping one loop
  per worker process alive in a background thread and submitting coroutines to it with
  ``run_coroutine_threadsafe`` *would* preserve the pool — and would also add a thread whose
  crash is invisible, a second place for a task to hang, and a pool that has to survive
  ``worker_max_tasks_per_child`` recycling. That is a performance ticket with a measurement
  attached, not a default.
* **The failure it prevents is silent.** A pooled asyncpg connection handed to a task running
  on a *different* loop does not raise a clean error; it hangs or produces
  ``got Future attached to a different loop``. Paying a connect is cheaper than debugging
  that.

**The trigger for revisiting it**: a task whose own runtime is close to the connect cost, or
a queue of many short tasks per second. Neither exists yet — the jobs in this repo are
ingest and refresh work measured in seconds.

Retries: the base sets the *spacing*, the task decides *what* is retryable
--------------------------------------------------------------------------

:class:`AnvexTask` deliberately sets **no** ``autoretry_for``. ``app/clients/`` has one exit,
``ExternalServiceError``, and it covers both "the vendor is down" (retry) and
"``NEWSAPI_API_KEY`` is blank" (retrying forever will not fill it in) — so blanket
auto-retry on the exception *type* would turn a permanent misconfiguration into an infinite
loop. A task branches on ``details["reason"]`` and calls
``self.retry(exc=exc, countdown=self.retry_countdown())`` when it means it.

That method exists because Celery's ``retry_backoff`` setting is **only** honoured by the
wrapper ``autoretry_for`` installs; a manual ``self.retry()`` ignores it and falls back to
the flat ``default_retry_delay``. Rather than ship a class attribute that silently does
nothing, the backoff is a method the task calls.
"""

from __future__ import annotations

import asyncio
import inspect
import random
from collections.abc import Callable, Coroutine
from typing import Any

from celery import Task

from app.db.engine import dispose_engine

#: Seconds before the *first* retry. Later ones double from here.
DEFAULT_RETRY_DELAY_SECONDS = 30
#: Ceiling on the doubling, so the tenth retry is not scheduled for tomorrow.
RETRY_BACKOFF_MAX_SECONDS = 600
#: How many times a task may retry itself before the failure is allowed to stand.
MAX_RETRIES = 3


def run_async[T](factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Run ``factory()`` on a fresh event loop and return its result.

    The only sanctioned way for synchronous code in ``app/jobs/`` to reach async Anvex code.
    Exceptions are **not** caught: whatever the coroutine raises propagates out of the task
    body, so Celery marks the task failed and the traceback reaches the logs. Swallowing one
    here would turn a broken job into a green one.

    The engine is disposed inside the loop before it closes — see the module docstring.
    """
    if inspect.iscoroutine(factory):
        factory.close()
        raise TypeError(
            "run_async takes a zero-argument callable, not a coroutine. "
            "Write run_async(lambda: work(arg)) so the coroutine is created inside the loop."
        )
    if not callable(factory):
        raise TypeError(f"run_async expected a callable, got {type(factory).__name__}.")
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "run_async was called from inside a running event loop. A Celery task body is "
            "synchronous; if you are already async, await the coroutine directly."
        )

    async def scoped() -> T:
        try:
            return await factory()
        finally:
            # Inside this loop, on purpose: the pool's connections belong to it and must
            # not be handed to the next task's loop.
            await dispose_engine()

    return asyncio.run(scoped())


class AnvexTask(Task):
    """The base class every Anvex task gets, wired in as the Celery app's ``task_cls``.

    It carries the retry *spacing* and nothing else. There is no ``run`` here and no
    dependency resolution: a task resolves its own collaborators in its async half, exactly
    as an API handler's dependency does, so the same service is reachable from both.
    """

    #: Celery honours this as the flat delay when a task calls ``self.retry()`` with no
    #: ``countdown``; :meth:`retry_countdown` is the exponential version.
    default_retry_delay = DEFAULT_RETRY_DELAY_SECONDS
    max_retries = MAX_RETRIES

    def retry_countdown(self, *, jitter: Callable[[], float] = random.random) -> float:
        """Seconds to wait before the next retry: exponential, capped, jittered.

        Doubling from :data:`DEFAULT_RETRY_DELAY_SECONDS` and capped at
        :data:`RETRY_BACKOFF_MAX_SECONDS`, then jittered **downward** into
        ``[delay/2, delay]`` so a fan-out that failed together does not retry together and
        recreate the thundering herd that caused the failure. Jitter is injectable for the
        same reason it is in ``app/clients/base.py``: a test asserts the bounds without a
        random number deciding whether it passes.
        """
        attempt = self.request.retries or 0
        delay = min(DEFAULT_RETRY_DELAY_SECONDS * (2**attempt), RETRY_BACKOFF_MAX_SECONDS)
        return delay * (0.5 + 0.5 * jitter())


__all__ = [
    "DEFAULT_RETRY_DELAY_SECONDS",
    "MAX_RETRIES",
    "RETRY_BACKOFF_MAX_SECONDS",
    "AnvexTask",
    "run_async",
]
