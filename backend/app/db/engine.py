"""The application's async SQLAlchemy engine.

One engine per process, created lazily so that importing ``app.db`` never opens a socket
and tests can override settings before the first use. The FastAPI lifespan (ANV-4) calls
:func:`dispose_engine` on shutdown.

**An engine is loop-bound and fork-hostile, and both follow from one fact: the pool holds
sockets.** The engine *object* is inert — building one performs no I/O — but every
connection it pools was opened by ``asyncpg`` inside a particular event loop and lives on a
particular file descriptor. So two things must never happen (ANV-21):

* **A pooled connection must not outlive the loop that opened it.** A Celery task runs its
  own ``asyncio.run``; that loop closes when the task ends, and a connection handed to the
  *next* task would belong to a loop that no longer exists. ``app/jobs/base.py``'s bridge
  therefore calls :func:`dispose_engine` inside the task's own loop, before it closes —
  which is why a worker gets no cross-task pooling, and why that cost is argued there.
* **A pooled connection must not survive a ``fork``.** A Celery prefork worker forks; both
  processes would then hold the same descriptor and interleave bytes on one Postgres
  session, which corrupts silently rather than failing loudly. :func:`reset_engine` is the
  child-side fix and is wired to Celery's ``worker_process_init`` signal. It is deliberately
  **synchronous and does no I/O**: a just-forked child has no event loop yet, and *closing*
  an inherited connection would tear it out from under the parent, turning a latent bug into
  a certain one.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.settings import Settings, get_settings

#: Persistent connections kept per process. The API runs a handful of uvicorn workers and
#: Postgres defaults to 100 connections, so this leaves plenty of headroom for the worker,
#: beat and interactive psql sessions.
POOL_SIZE = 10
#: Extra short-lived connections allowed above ``POOL_SIZE`` during a burst.
MAX_OVERFLOW = 5
#: Seconds a request waits for a free connection before erroring rather than hanging.
POOL_TIMEOUT_SECONDS = 30
#: Recycle below the typical 1h idle timeout of managed Postgres/proxies (RDS, pgbouncer).
POOL_RECYCLE_SECONDS = 1800

_engine: AsyncEngine | None = None


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    """Build a new async engine from ``settings`` (defaults to the cached settings).

    Creating an engine performs no I/O; the first connection is opened on demand.
    """
    settings = settings or get_settings()
    return create_async_engine(
        settings.postgres_dsn,
        # Cheap liveness check on checkout — without it, a connection killed by a restart
        # or an idle timeout surfaces as a random 500 on the next request.
        pool_pre_ping=True,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_timeout=POOL_TIMEOUT_SECONDS,
        pool_recycle=POOL_RECYCLE_SECONDS,
        echo=False,
    )


def get_engine() -> AsyncEngine:
    """Return the process-wide engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


def current_engine() -> AsyncEngine | None:
    """The process-wide engine if one has been built, otherwise ``None``.

    The read-only twin of :func:`get_engine`, which exists so that "has this process opened
    a pool yet?" is answerable **without** answering it in the affirmative. ANV-21's fork
    rule — a worker parent must reach the fork with no engine — is only assertable because
    of this: calling :func:`get_engine` to check would create the very thing being ruled out.
    """
    return _engine


def reset_engine() -> None:
    """Forget the engine and abandon its pool **without closing anything**.

    The child-side half of the fork rule. Call it immediately after a ``fork`` (Celery's
    ``worker_process_init``): the child inherits the parent's engine object and, with it,
    every descriptor in the parent's pool. Replacing the pool means the child can never
    check one of those out; not closing them means the parent's connections keep working.
    That is exactly SQLAlchemy's documented ``dispose(close=False)`` recipe for multiprocess
    use, reached through ``sync_engine`` because :meth:`AsyncEngine.dispose` is a coroutine
    and there is no loop in a just-forked child to await it in.

    Synchronous, I/O-free and safe to call when no engine was ever created — a worker boot
    hook cannot be allowed to fail because the parent happened not to touch the database.
    """
    global _engine
    if _engine is not None:
        engine, _engine = _engine, None
        # close=False: swap in an empty pool, leave the inherited sockets alone.
        engine.sync_engine.dispose(close=False)


async def dispose_engine() -> None:
    """Close every pooled connection and forget the engine.

    Safe to call when no engine was ever created. The next :func:`get_engine` builds a
    fresh one, which is what makes this usable both from an app shutdown hook and from
    tests that repoint the settings.
    """
    global _engine
    if _engine is not None:
        engine, _engine = _engine, None
        await engine.dispose()
