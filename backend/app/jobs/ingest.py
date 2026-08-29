"""The scheduled intraday ingest: a fan-out task and the per-symbol task it dispatches.

The last piece of ``AverageInvestorService``. That repo ran a ``for`` loop on an EC2 box,
sleeping ten seconds between AlphaVantage calls to stay under the free tier's five a minute.
This is the same work, scheduled, idempotent, retry-classified — and with **nothing
sleeping anywhere**.

Two tasks, and the split is the rate limit
------------------------------------------

:func:`ingest_all` is beat's entry point. It makes **no vendor call at all**: it asks
:meth:`~app.services.ingest.IngestService.plan` what this run should fetch, and publishes
one :func:`ingest_symbol` message per call with a ``countdown``. :func:`ingest_symbol` makes
**exactly one** vendor call — one symbol, one month — and writes what comes back.

That one-call-per-task property is the whole design. It is what lets the pacing be
arithmetic on a message (:func:`~app.domain.ingest.dispatch_delays` →
``0, 15, 30, … seconds``) instead of a wait inside a worker:

* ``time.sleep(10)`` — the old ETL's answer — blocks a prefork child. A worker with
  concurrency 4 running a twenty-call fan-out would have every child asleep for most of five
  minutes, and any *other* job scheduled in that window simply waits.
* ``await asyncio.sleep(10)`` is not the fix it looks like. It yields the event loop, but a
  Celery task owns its child process for its whole duration and ``run_async`` gives it a
  loop with nothing else on it — so the worker slot is held exactly as long. Non-blocking in
  the wrong place is still blocked.
* A ``countdown`` costs nothing, because **the work does not exist yet**. The message sits
  in the broker; no worker, no loop and no connection is committed to it until it is due.

What it costs, stated rather than discovered
--------------------------------------------

1. **It is a pacing scheme, not a rate limiter.** Nothing counts calls. Two overlapping
   fan-outs — a manual ``ingest_all`` beside a scheduled one — would double the rate, and
   the vendor would answer with the 200-that-means-429 ANV-18 parses. That is survivable
   (``rate_limited`` is retryable and :meth:`~app.jobs.base.AnvexTask.retry_countdown`
   backs off), and a real limiter means a shared token bucket in Redis — a distributed
   lock's worth of machinery for a job that runs hourly. The mitigation is arithmetic
   instead: :data:`~app.domain.ingest.MAX_CALLS_PER_RUN` times the spacing must stay inside the
   beat interval, and ``tests/unit/test_jobs_celery_app.py`` asserts it.
2. **A countdown is a reservation.** Celery hands an ETA/countdown message to a worker,
   which holds it in memory until it is due. With ``worker_prefetch_multiplier=1`` and
   ``acks_late``, a long fan-out is a queue of reserved messages, and a worker restart
   mid-fan-out redelivers them. Both are fine *because* every target is idempotent — but
   the fan-out is deliberately bounded rather than unbounded for exactly this reason.
3. **The plan is stale by the time it runs.** A target dispatched at T+285s was planned at
   T. So :meth:`~app.services.ingest.IngestService.ingest_month` re-reads the watermark
   itself rather than trusting the plan, and every dispatched message carries an
   ``expires`` — a run that never got consumed is dropped rather than executed an hour
   late, and the next tick is the retry (ANV-21's rule for beat entries, applied to the
   messages a beat entry produces).
4. **A big roster converges rather than completing.** Twenty calls an hour is the budget;
   :func:`~app.domain.ingest.fan_out_order` spends it on every stock's current month first,
   so a roster larger than the budget stays current and backfills more slowly.

Retryability is decided here, one reason at a time
--------------------------------------------------

``AnvexTask`` has no ``autoretry_for`` on purpose (ANV-21): ``app/clients/``'s single exit
covers both "the vendor is down" and "``ALPHAVANTAGE_API_KEY`` is blank", and retrying the
second forever will not fill it in. So :func:`ingest_symbol` catches
:class:`~app.domain.errors.ExternalServiceError`, branches on ``details["reason"]``, and
retries only :data:`RETRYABLE_REASONS` — which is **derived from**
``app.clients.base.RETRYABLE`` rather than retyped, so the job and the client cannot come to
disagree about what a transient failure is.

Everything else is allowed to fail: ``not_configured`` (nothing will change until a
deployment does), ``client_error`` (AlphaVantage's 200-with-``Error Message`` for an unknown
symbol — a real defect in the roster, and rescheduling it would loop until the retry budget
ran out), ``malformed_response`` (a vendor answering with HTML is broken, not blipping — the
client does not retry it either), and :class:`~app.domain.errors.NotFoundError` for a ticker
that is not tracked.
"""

from __future__ import annotations

from typing import Any, Final

import structlog

from app.clients.alphavantage import AlphaVantageClient
from app.clients.base import RETRYABLE
from app.db.session import get_session
from app.domain.errors import ExternalServiceError
from app.domain.ingest import dispatch_delays
from app.jobs.base import AnvexTask, run_async
from app.jobs.celery_app import celery_app
from app.services.ingest import IngestService, dispatch_plan
from app.settings import get_settings

logger = structlog.get_logger("anvex.jobs.ingest")

#: Explicit names, so renaming this module cannot orphan a queued message or a beat entry
#: (``CLAUDE.md`` §3). ``app/jobs/celery_app.py``'s sweep asserts the convention.
INGEST_ALL_TASK_NAME: Final = "jobs.ingest.ingest_all"
INGEST_SYMBOL_TASK_NAME: Final = "jobs.ingest.ingest_symbol"

#: Seconds a dispatched target stays runnable **after** its own countdown. Beyond that the
#: message is dropped unexecuted: a target that has waited an extra ten minutes has been
#: superseded by the next fan-out, which will plan the same month again from a fresher
#: watermark. Same argument as a beat entry's ``expires``, one level down.
DISPATCH_EXPIRY_MARGIN_SECONDS: Final[int] = 600

#: The ``details["reason"]`` values worth retrying. Derived from the client layer's own
#: :data:`~app.clients.base.RETRYABLE` — ``transport_error``, ``server_error``,
#: ``rate_limited`` — so a new transient failure classified there is retried here without
#: an edit. Everything absent is permanent, including ``not_configured``, which is not a
#: :class:`~app.clients.base.Failure` at all.
RETRYABLE_REASONS: Final[frozenset[str]] = frozenset(str(failure) for failure in RETRYABLE)


# ---------------------------------------------------------------------------------------
# The fan-out
# ---------------------------------------------------------------------------------------


async def _ingest_all() -> dict[str, Any]:
    """Plan the run and publish one message per vendor call.

    **No clock is read here.** ``CLAUDE.md`` §4 makes a service the only layer allowed to
    read one, and :meth:`~app.services.ingest.IngestService.plan` does — once, at the top,
    and hands that single value to every month rule in its loop. A task that read its own
    would be a second answer to "what time is it" in a code path that already has one.

    The publish loop is synchronous inside a coroutine, which is deliberate rather than
    overlooked: Celery has no async producer, ``kombu``'s publish is the same blocking call
    an API handler makes when it queues a task, and by this point the session is closed and
    there is nothing else on this loop to starve. Doing it *after* the ``async with`` is the
    part that matters — a fan-out must not hold a Postgres connection while it talks to
    Redis.
    """
    settings = get_settings()

    async with AlphaVantageClient(settings) as client, get_session() as session:
        # One `AlphaVantageClient` for the whole task, never one per call and never one at
        # import or worker boot: it owns an `httpx.AsyncClient` whose pool is bound to this
        # task's loop, and a prefork worker forks (ANV-20/ANV-21). `plan` makes no vendor
        # call, so this one opens no socket at all — the base builds its transport lazily.
        targets = await IngestService(session, settings, client=client).plan()

    dispatched = dispatch_plan(targets, dispatch_delays(len(targets)))
    for message in dispatched:
        ingest_symbol.apply_async(
            kwargs=message["kwargs"],
            countdown=message["countdown"],
            expires=message["countdown"] + DISPATCH_EXPIRY_MARGIN_SECONDS,
        )

    summary = {
        "dispatched": len(dispatched),
        "spans_seconds": dispatched[-1]["countdown"] if dispatched else 0,
    }
    logger.info("jobs.ingest.dispatched", **summary)
    return summary


@celery_app.task(name=INGEST_ALL_TASK_NAME, bind=True)
def ingest_all(self: AnvexTask) -> dict[str, Any]:
    """Fan out one ingest run across every tracked security. Beat's entry point.

    Not retried: it makes no vendor call and its only failure mode is the database being
    unreachable, which the next tick handles better than a retry would. If it fails, the
    run is simply lost — which is safe, because nothing was dispatched.
    """
    return run_async(lambda: _ingest_all())


# ---------------------------------------------------------------------------------------
# One symbol, one month, one vendor call
# ---------------------------------------------------------------------------------------


async def _ingest_symbol(*, ticker: str, month: str) -> dict[str, Any]:
    """Fetch and store one symbol-month. Resolves collaborators, calls **one** service."""
    settings = get_settings()
    async with AlphaVantageClient(settings) as client, get_session() as session:
        report = await IngestService(session, settings, client=client).ingest_month(
            ticker=ticker, month=month
        )
    return report.as_result()


def is_retryable(error: ExternalServiceError) -> bool:
    """Whether this vendor failure is worth trying again.

    Keyed on the machine-readable ``details["reason"]`` and never on the message — that is
    the whole reason ANV-17 put a reason in ``details``, and a message match breaks the
    first time a string is reworded (``CLAUDE.md`` §4).

    A missing ``reason`` is **not** retried. Every failure ``app/clients/`` raises carries
    one, so an error without it did not come from the layer this branch is about, and
    guessing "transient" would turn an unknown bug into a retry loop.
    """
    return str(error.details.get("reason", "")) in RETRYABLE_REASONS


@celery_app.task(name=INGEST_SYMBOL_TASK_NAME, bind=True)
def ingest_symbol(self: AnvexTask, ticker: str, month: str) -> dict[str, Any]:
    """Ingest one ``YYYY-MM`` of five-minute candles for one symbol. **Idempotent.**

    Safe to run twice — ``task_acks_late`` and beat between them guarantee it will be — and
    the two mechanisms that make it so are in ``app/services/ingest.py``'s docstring.

    The only ``try`` in this package, and it exists to *classify* rather than to swallow: a
    retryable vendor failure is rescheduled with the base class's exponential, capped,
    downward-jittered backoff, and everything else propagates so the task is red. Anything
    that is not an ``ExternalServiceError`` — a ``NotFoundError`` for an untracked ticker, a
    ``ValidationError`` for a malformed month, a database failure — is a permanent problem
    with the message or the roster and is never retried.
    """
    try:
        return run_async(lambda: _ingest_symbol(ticker=ticker, month=month))
    except ExternalServiceError as error:
        if not is_retryable(error):
            logger.warning(
                "jobs.ingest.permanent_failure",
                ticker=ticker,
                month=month,
                reason=error.details.get("reason"),
            )
            raise
        countdown = self.retry_countdown()
        logger.info(
            "jobs.ingest.retrying",
            ticker=ticker,
            month=month,
            reason=error.details.get("reason"),
            countdown=countdown,
            attempt=(self.request.retries or 0) + 1,
        )
        raise self.retry(exc=error, countdown=countdown) from error


__all__ = [
    "DISPATCH_EXPIRY_MARGIN_SECONDS",
    "INGEST_ALL_TASK_NAME",
    "INGEST_SYMBOL_TASK_NAME",
    "RETRYABLE_REASONS",
    "ingest_all",
    "ingest_symbol",
    "is_retryable",
]
