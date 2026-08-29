"""Unit tests for the sync/async bridge — ``app.jobs.base``.

This is the module ANV-21 exists to get right: every job after it crosses this bridge, and a
bridge that swallows an error or leaks a connection pool is a defect that only shows up as a
green worker doing nothing. So the tests here are about **properties** rather than about the
implementation:

* whatever the async half returns is what the task returns;
* whatever it raises reaches Celery, so a broken job is a red one;
* nothing loop-bound survives one task and reaches the next.

No I/O: building a SQLAlchemy engine opens no socket, so the engine-lifetime properties are
assertable at unit speed against the real engine module.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from celery import Celery
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import engine as db_engine
from app.jobs.base import (
    DEFAULT_RETRY_DELAY_SECONDS,
    MAX_RETRIES,
    RETRY_BACKOFF_MAX_SECONDS,
    AnvexTask,
    run_async,
)
from app.jobs.celery_app import create_celery_app
from app.settings import Settings


@pytest.fixture(autouse=True)
def _isolated_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Reset the process-wide engine around every test, so none of these leak into another."""
    monkeypatch.setattr(db_engine, "_engine", None)
    yield
    monkeypatch.setattr(db_engine, "_engine", None)


class StubServiceError(RuntimeError):
    """Stands in for a domain error raised inside an async service."""


class FakeReportService:
    """The shape a task actually talks to: one ``async`` method, one use case.

    Deliberately not a mock. The bridge's contract is about a real coroutine's result and a
    real exception's traceback, and a mock's return value would prove neither.
    """

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    async def summarise(self, *, symbol: str) -> dict[str, str]:
        self.calls.append(symbol)
        await asyncio.sleep(0)
        if self.fail:
            raise StubServiceError(f"no data for {symbol}")
        return {"symbol": symbol, "verdict": "hold"}


# ------------------------------------------------------------------ the bridge's contract


def test_bridge_returns_what_the_async_service_returned() -> None:
    service = FakeReportService()

    result = run_async(lambda: service.summarise(symbol="AAPL"))

    assert result == {"symbol": "AAPL", "verdict": "hold"}
    assert service.calls == ["AAPL"]


def test_an_exception_inside_the_service_propagates_out_of_the_bridge() -> None:
    """A failed job must be a failed job. Swallowing here would make it a green one."""
    service = FakeReportService(fail=True)

    with pytest.raises(StubServiceError, match="no data for MSFT"):
        run_async(lambda: service.summarise(symbol="MSFT"))


def test_the_traceback_still_points_at_the_service() -> None:
    """The frame that raised has to survive the loop boundary, or debugging a job is guesswork."""
    service = FakeReportService(fail=True)

    with pytest.raises(StubServiceError) as excinfo:
        run_async(lambda: service.summarise(symbol="MSFT"))

    frames = [frame.name for frame in excinfo.traceback]
    assert "summarise" in frames


def test_the_body_runs_on_a_real_event_loop() -> None:
    async def body() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    loop = run_async(body)

    assert isinstance(loop, asyncio.AbstractEventLoop)
    assert loop.is_closed()  # the bridge owns it and closes it


def test_each_call_gets_its_own_loop() -> None:
    loops = [run_async(lambda: _current_loop()) for _ in range(2)]

    assert loops[0] is not loops[1]


async def _current_loop() -> asyncio.AbstractEventLoop:
    return asyncio.get_running_loop()


# ------------------------------------------------------------------- the factory signature


def test_a_coroutine_is_refused_with_the_fix_in_the_message() -> None:
    """``run_async(work())`` builds the coroutine outside the loop. Say so, loudly."""
    service = FakeReportService()

    with pytest.raises(TypeError, match="zero-argument callable"):
        run_async(service.summarise(symbol="AAPL"))  # type: ignore[arg-type]


def test_refusing_a_coroutine_closes_it() -> None:
    """The refusal is the error; it must not also emit a "never awaited" warning later.

    ``cr_frame`` is ``None`` exactly once a coroutine has been closed, so this is the
    deterministic form of that assertion — the warning itself only fires at collection time.
    """
    service = FakeReportService()
    coro = service.summarise(symbol="AAPL")

    with pytest.raises(TypeError):
        run_async(coro)  # type: ignore[arg-type]

    assert coro.cr_frame is None


def test_a_non_callable_is_refused() -> None:
    with pytest.raises(TypeError, match="expected a callable"):
        run_async(42)  # type: ignore[arg-type]


async def test_calling_the_bridge_from_inside_a_loop_is_refused() -> None:
    """``asyncio.run`` would raise something obscure. Raise something that says what to do."""
    with pytest.raises(RuntimeError, match="already async"):
        run_async(lambda: _current_loop())


# -------------------------------------------------------------- engine lifetime (the point)


def test_no_engine_crosses_a_task_boundary() -> None:
    """The property the fork/loop rules exist for.

    A pooled ``asyncpg`` connection belongs to the loop that opened it. If the engine that a
    task used were handed to the next task — which runs on a *different* loop — the failure
    is a hang or ``got Future attached to a different loop``, not a clean error. So: two
    runs, two engines, and never the same one twice.
    """
    seen: list[AsyncEngine] = []

    async def body() -> None:
        seen.append(db_engine.get_engine())

    run_async(body)
    run_async(body)

    assert len(seen) == 2
    assert seen[0] is not seen[1]


def test_the_engine_is_gone_when_the_task_ends() -> None:
    async def body() -> None:
        db_engine.get_engine()
        assert db_engine.current_engine() is not None

    run_async(body)

    assert db_engine.current_engine() is None


def test_the_engine_is_disposed_even_when_the_task_fails() -> None:
    """A failing job must not leave a pool bound to a loop that is about to close."""

    async def body() -> None:
        db_engine.get_engine()
        raise StubServiceError("boom")

    with pytest.raises(StubServiceError):
        run_async(body)

    assert db_engine.current_engine() is None


def test_a_task_that_never_touches_the_database_costs_nothing() -> None:
    """The bridge's cleanup is unconditional, so it has to be a no-op when there is no engine."""

    async def body() -> str:
        return "ok"

    assert run_async(body) == "ok"
    assert db_engine.current_engine() is None


# ------------------------------------------------- the bridge inside a real task object


@pytest.fixture
def eager_app() -> Celery:
    """A throwaway Celery app that runs tasks in-process.

    Deliberately **not** the application's ``celery_app``: registering a stub task there
    would leave it in the production registry for the rest of the session, where the naming
    sweep in ``test_jobs_celery_app.py`` would find it. A separate app costs nothing —
    Celery apps are cheap and this one never opens a connection.

    A separate app is not enough on its own, though, and the reason is worth knowing:
    ``@app.task`` defaults to ``shared=True``, which registers the task on **every** Celery
    app finalized afterwards, including the application's. Each stub below therefore passes
    ``shared=False``.
    """
    app = create_celery_app(Settings(_env_file=None))
    app.conf.update(task_always_eager=True, task_eager_propagates=False)
    return app


def test_a_task_returns_what_the_async_service_returned(eager_app: Celery) -> None:
    """The whole shape, end to end: sync task body → bridge → async service → result."""
    service = FakeReportService()

    @eager_app.task(name="tests.jobs.summarise", shared=False)
    def summarise(symbol: str) -> dict[str, str]:
        return run_async(lambda: service.summarise(symbol=symbol))

    result = summarise.delay("AAPL")

    assert result.successful()
    assert result.get() == {"symbol": "AAPL", "verdict": "hold"}


def test_an_exception_in_the_service_becomes_a_task_failure(eager_app: Celery) -> None:
    """Not a success carrying ``None``, and not a swallowed log line.

    This is the failure mode the bridge exists to prevent: a job that reports green while
    having done nothing is worse than one that is red.
    """
    service = FakeReportService(fail=True)

    @eager_app.task(name="tests.jobs.summarise_broken", shared=False)
    def summarise(symbol: str) -> dict[str, str]:
        return run_async(lambda: service.summarise(symbol=symbol))

    result = summarise.delay("MSFT")

    assert result.failed()
    assert result.state == "FAILURE"
    assert isinstance(result.result, StubServiceError)
    assert str(result.result) == "no data for MSFT"


def test_a_task_built_from_the_app_uses_the_anvex_base(eager_app: Celery) -> None:
    @eager_app.task(name="tests.jobs.trivial", shared=False)
    def trivial() -> str:
        return "ok"

    assert isinstance(trivial, AnvexTask)


# ------------------------------------------------------------------------- retry spacing


class _Request:
    def __init__(self, retries: int) -> None:
        self.retries = retries


class _CountdownTask(AnvexTask):
    """`AnvexTask` with its request stubbed, so the spacing is testable without a broker."""

    def __init__(self, retries: int) -> None:
        self._request = _Request(retries)

    @property  # type: ignore[override]
    def request(self) -> _Request:
        return self._request


@pytest.mark.parametrize(
    ("retries", "expected_full_delay"),
    [(0, 30), (1, 60), (2, 120), (3, 240), (10, RETRY_BACKOFF_MAX_SECONDS)],
)
def test_retry_countdown_doubles_and_then_caps(retries: int, expected_full_delay: int) -> None:
    task = _CountdownTask(retries)

    assert task.retry_countdown(jitter=lambda: 1.0) == expected_full_delay


@pytest.mark.parametrize("retries", [0, 1, 2, 3])
def test_retry_countdown_jitters_downward_only(retries: int) -> None:
    """Never *longer* than the nominal delay, and never less than half of it.

    Downward jitter is what stops a fan-out that failed together retrying together — the
    same rule ``app/clients/base.py`` applies to its own waits.
    """
    task = _CountdownTask(retries)
    full = task.retry_countdown(jitter=lambda: 1.0)

    assert task.retry_countdown(jitter=lambda: 0.0) == pytest.approx(full / 2)
    assert 0.5 * full <= task.retry_countdown() <= full


def test_retry_countdown_survives_a_task_that_has_never_run() -> None:
    """``request.retries`` is ``None`` outside a delivery; the first delay must still be sane."""
    task = _CountdownTask(retries=None)  # type: ignore[arg-type]

    assert task.retry_countdown(jitter=lambda: 1.0) == DEFAULT_RETRY_DELAY_SECONDS


def test_the_base_sets_spacing_but_never_decides_what_is_retryable() -> None:
    """No ``autoretry_for``: ``ExternalServiceError`` covers both an outage and a blank key.

    Retrying a missing credential forever is not resilience, it is a loop — so *what* is
    worth retrying stays the job's decision and only the spacing is shared.
    """
    assert AnvexTask.max_retries == MAX_RETRIES
    assert AnvexTask.default_retry_delay == DEFAULT_RETRY_DELAY_SECONDS
    assert getattr(AnvexTask, "autoretry_for", ()) == ()
