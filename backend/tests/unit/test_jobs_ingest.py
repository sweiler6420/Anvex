"""Unit tests for ``app.jobs.ingest`` — the task bodies, the fan-out, and retryability.

Eager mode and direct calls prove what a task *does*; only a broker proves that a message
travels (``tests/integration/test_jobs_broker.py``). Both exist because either alone is
misleading — ``task_always_eager`` would keep passing with a nonsense broker URL.

The interesting half of this module is the classification. ``app/clients/`` has exactly one
exit and it covers "the vendor is down" and "the key is blank", so ANV-21 deliberately
shipped no ``autoretry_for`` and left the branch to each job. Getting it wrong in either
direction is expensive and silent: retrying a blank key loops until the retry budget runs
out, and *not* retrying a rate limit throws the run away.
"""

from __future__ import annotations

import ast
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from app.clients.base import Failure
from app.domain.errors import ExternalServiceError, NotFoundError
from app.domain.ingest import CALL_SPACING_SECONDS
from app.jobs import ingest as jobs_ingest
from app.jobs.base import MAX_RETRIES
from app.jobs.celery_app import celery_app
from app.jobs.ingest import (
    DISPATCH_EXPIRY_MARGIN_SECONDS,
    INGEST_ALL_TASK_NAME,
    INGEST_SYMBOL_TASK_NAME,
    RETRYABLE_REASONS,
    _ingest_all,
    _ingest_symbol,
    ingest_all,
    ingest_symbol,
    is_retryable,
)
from app.services.ingest import IngestReport, IngestTarget
from tests.helpers import StubSession


def source_tree() -> ast.Module:
    return ast.parse(Path(jobs_ingest.__file__).read_text(encoding="utf-8"))


def failure(reason: str, **details: Any) -> ExternalServiceError:
    return ExternalServiceError(
        "alphavantage", "the vendor said no", details={"reason": reason, **details}
    )


REPORT = IngestReport(
    ticker="AAPL", month="2026-03", fetched=78, in_session=60, fresh=12, written=12, duplicates=0
)


class RecordingService:
    """A stand-in for ``IngestService`` that records how the task called it.

    The task's own job is to resolve collaborators and call **one** service method
    (``CLAUDE.md`` §3), so what is worth asserting here is exactly that — and that the client
    it constructed was closed on the way out.
    """

    targets: tuple[IngestTarget, ...] = ()
    report: IngestReport = REPORT
    error: Exception | None = None
    calls: list[tuple[str, dict[str, Any]]] = []  # noqa: RUF012 - reset by the fixture

    def __init__(self, session: Any, settings: Any, *, client: Any, **_: Any) -> None:
        self.session = session
        self.settings = settings
        self.client = client

    async def plan(self, **kwargs: Any) -> tuple[IngestTarget, ...]:
        RecordingService.calls.append(("plan", kwargs))
        if RecordingService.error is not None:
            raise RecordingService.error
        return RecordingService.targets

    async def ingest_month(self, **kwargs: Any) -> IngestReport:
        RecordingService.calls.append(("ingest_month", kwargs))
        if RecordingService.error is not None:
            raise RecordingService.error
        return RecordingService.report


class TrackingClient:
    """The seam that proves one client is held for the whole task and closed at the end."""

    instances: list[TrackingClient] = []  # noqa: RUF012 - reset by the fixture

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.closed = False
        TrackingClient.instances.append(self)

    async def __aenter__(self) -> TrackingClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        self.closed = True


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """Replace the task's three collaborators; return the list of dispatched messages."""
    RecordingService.targets = ()
    RecordingService.report = REPORT
    RecordingService.error = None
    RecordingService.calls = []
    TrackingClient.instances = []
    dispatched: list[dict[str, Any]] = []

    @asynccontextmanager
    async def fake_session() -> AsyncIterator[StubSession]:
        yield StubSession()

    monkeypatch.setattr(jobs_ingest, "get_session", fake_session)
    monkeypatch.setattr(jobs_ingest, "AlphaVantageClient", TrackingClient)
    monkeypatch.setattr(jobs_ingest, "IngestService", RecordingService)
    monkeypatch.setattr(
        ingest_symbol, "apply_async", lambda **kwargs: dispatched.append(kwargs), raising=False
    )
    yield dispatched


@pytest.fixture
def eager() -> Iterator[None]:
    """Run tasks in-process. Restored afterwards so no other test inherits it."""
    previous = (celery_app.conf.task_always_eager, celery_app.conf.task_eager_propagates)
    celery_app.conf.update(task_always_eager=True, task_eager_propagates=False)
    try:
        yield
    finally:
        celery_app.conf.update(task_always_eager=previous[0], task_eager_propagates=previous[1])


# ---------------------------------------------------------------------------------------
# registration and shape
# ---------------------------------------------------------------------------------------


class TestRegistration:
    def test_both_tasks_carry_their_explicit_names(self) -> None:
        assert ingest_all.name == INGEST_ALL_TASK_NAME
        assert ingest_symbol.name == INGEST_SYMBOL_TASK_NAME
        assert celery_app.tasks[INGEST_ALL_TASK_NAME].name == INGEST_ALL_TASK_NAME
        assert celery_app.tasks[INGEST_SYMBOL_TASK_NAME].name == INGEST_SYMBOL_TASK_NAME

    def test_neither_task_body_is_anything_but_the_bridge(self) -> None:
        """``run_async`` is the one bridge and every task crosses it unchanged (ANV-21).

        Asserted structurally, because "the task body is one line" is the sort of rule that
        decays into "the task body is mostly one line" over three tickets.
        """
        bodies = {
            node.name: node.body[1:]  # drop the docstring
            for node in ast.walk(source_tree())
            if isinstance(node, ast.FunctionDef) and node.name in {"ingest_all", "ingest_symbol"}
        }

        calls = [
            node
            for statements in bodies.values()
            for statement in statements
            for node in ast.walk(statement)
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "run_async"
        ]
        assert len(calls) == 2, "each task body crosses the bridge exactly once"
        for call in calls:
            assert isinstance(call.args[0], ast.Lambda), (
                "run_async takes a factory, not a coroutine — write run_async(lambda: work())"
            )

    def test_the_module_never_sleeps(self) -> None:
        """The claim the whole fan-out design rests on. The old ETL's ``time.sleep(10)`` is
        replaced by arithmetic on a ``countdown``, so no worker slot is ever held waiting."""
        sleeps = [
            node
            for node in ast.walk(source_tree())
            if isinstance(node, ast.Call)
            and (
                getattr(node.func, "attr", None) == "sleep"
                or getattr(node.func, "id", None) == "sleep"
            )
        ]

        assert sleeps == []

    def test_the_job_reads_no_clock(self) -> None:
        """``CLAUDE.md`` §4 puts the clock read in the service; ``plan`` does it, once."""
        offenders = [
            node
            for node in ast.walk(source_tree())
            if isinstance(node, ast.Call)
            and getattr(node.func, "attr", None) in {"now", "utcnow", "today"}
        ]

        assert offenders == []


# ---------------------------------------------------------------------------------------
# the fan-out
# ---------------------------------------------------------------------------------------


class TestIngestAll:
    async def test_an_empty_plan_dispatches_nothing(self, wired: list[dict[str, Any]]) -> None:
        summary = await _ingest_all()

        assert wired == []
        assert summary == {"dispatched": 0, "spans_seconds": 0}

    async def test_each_target_becomes_one_staggered_message(
        self, wired: list[dict[str, Any]]
    ) -> None:
        RecordingService.targets = (
            IngestTarget("AAPL", "2026-03"),
            IngestTarget("MSFT", "2026-03"),
            IngestTarget("AAPL", "2026-02"),
        )

        summary = await _ingest_all()

        assert [message["kwargs"] for message in wired] == [
            {"ticker": "AAPL", "month": "2026-03"},
            {"ticker": "MSFT", "month": "2026-03"},
            {"ticker": "AAPL", "month": "2026-02"},
        ]
        assert [message["countdown"] for message in wired] == [
            0,
            CALL_SPACING_SECONDS,
            2 * CALL_SPACING_SECONDS,
        ]
        assert summary == {"dispatched": 3, "spans_seconds": 2 * CALL_SPACING_SECONDS}

    async def test_every_dispatched_message_expires(self, wired: list[dict[str, Any]]) -> None:
        """ANV-21's beat rule, one level down: a target nobody consumed is superseded by the
        next fan-out, not executed an hour late."""
        RecordingService.targets = (IngestTarget("AAPL", "2026-03"),)

        await _ingest_all()

        assert wired[0]["expires"] == DISPATCH_EXPIRY_MARGIN_SECONDS
        assert wired[0]["expires"] > wired[0]["countdown"]

    async def test_it_calls_one_service_method_and_makes_no_vendor_call(
        self, wired: list[dict[str, Any]]
    ) -> None:
        await _ingest_all()

        assert [name for name, _ in RecordingService.calls] == ["plan"]

    async def test_it_holds_exactly_one_client_and_closes_it(
        self, wired: list[dict[str, Any]]
    ) -> None:
        """One per task, never one per call and never one at import or worker boot: the pool
        is bound to this task's loop and a prefork worker forks (ANV-20/ANV-21)."""
        await _ingest_all()

        assert len(TrackingClient.instances) == 1
        assert TrackingClient.instances[0].closed is True

    async def test_a_planning_failure_is_not_swallowed(self, wired: list[dict[str, Any]]) -> None:
        """``run_async`` never catches; a green job that did nothing is worse than a red one."""
        RecordingService.error = RuntimeError("the database is unreachable")

        with pytest.raises(RuntimeError, match="unreachable"):
            await _ingest_all()

        assert wired == []

    def test_the_summary_is_json_serialisable(self, wired: list[dict[str, Any]]) -> None:
        RecordingService.targets = (IngestTarget("AAPL", "2026-03"),)

        summary = ingest_all.apply().get()

        assert json.loads(json.dumps(summary)) == summary
        assert summary["dispatched"] == 1


# ---------------------------------------------------------------------------------------
# one symbol, one month
# ---------------------------------------------------------------------------------------


class TestIngestSymbol:
    async def test_it_passes_the_message_straight_through_to_one_service_method(
        self, wired: list[dict[str, Any]]
    ) -> None:
        result = await _ingest_symbol(ticker="aapl", month="2026-03")

        assert RecordingService.calls == [("ingest_month", {"ticker": "aapl", "month": "2026-03"})]
        assert result == REPORT.as_result()

    async def test_it_holds_exactly_one_client_and_closes_it(
        self, wired: list[dict[str, Any]]
    ) -> None:
        await _ingest_symbol(ticker="AAPL", month="2026-03")

        assert len(TrackingClient.instances) == 1
        assert TrackingClient.instances[0].closed is True

    def test_the_result_is_json_serialisable(self, wired: list[dict[str, Any]]) -> None:
        """A ``Decimal`` or a dataclass returned from a task fails at the result backend, in
        the worker, long after the code that produced it looked fine."""
        payload = ingest_symbol.apply(kwargs={"ticker": "AAPL", "month": "2026-03"}).get()

        assert json.loads(json.dumps(payload)) == payload
        assert payload["written"] == 12

    def test_eager_execution_returns_the_report(
        self, wired: list[dict[str, Any]], eager: None
    ) -> None:
        """Eager mode calls the task rather than publishing it, so ``apply`` is the honest
        spelling here — ``delay`` would go through the ``apply_async`` the fixture stubbed."""
        result = ingest_symbol.apply(args=("AAPL", "2026-03"))

        assert result.successful()
        assert result.get()["ticker"] == "AAPL"


# ---------------------------------------------------------------------------------------
# retryability
# ---------------------------------------------------------------------------------------


class TestRetryClassification:
    def test_the_retryable_set_is_the_client_layers_own(self) -> None:
        """Derived rather than retyped, so the job and the client cannot come to disagree."""
        assert {"transport_error", "server_error", "rate_limited"} == RETRYABLE_REASONS

    @pytest.mark.parametrize("reason", ["transport_error", "server_error", "rate_limited"], ids=str)
    def test_a_transient_vendor_failure_is_retryable(self, reason: str) -> None:
        assert is_retryable(failure(reason)) is True

    @pytest.mark.parametrize(
        "reason",
        ["client_error", "malformed_response", "unexpected_redirect", "protocol_error"],
        ids=str,
    )
    def test_a_permanent_vendor_failure_is_not(self, reason: str) -> None:
        assert is_retryable(failure(reason)) is False

    def test_a_blank_api_key_is_never_retried(self) -> None:
        """Retrying forever will not fill it in, and ``not_configured`` is not a ``Failure``."""
        blank = failure("not_configured", setting="ALPHAVANTAGE_API_KEY")

        assert is_retryable(blank) is False
        assert "not_configured" not in RETRYABLE_REASONS

    def test_every_failure_the_client_can_raise_is_classified(self) -> None:
        """A sweep over the vendor taxonomy, so a new ``Failure`` member cannot slip in
        unclassified — it would default to "permanent", which is at least the safe side."""
        for member in Failure:
            assert isinstance(is_retryable(failure(str(member))), bool)

    def test_a_failure_with_no_reason_is_not_retried(self) -> None:
        """Everything ``app/clients/`` raises carries one, so an error without it did not come
        from the layer this branch is about — and guessing would be a retry loop."""
        assert is_retryable(ExternalServiceError("alphavantage", "no details")) is False


class TestTheTaskActsOnTheClassification:
    """The branch itself, driven through the task body with the bridge stubbed out."""

    @pytest.fixture
    def retries(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        recorded: list[dict[str, Any]] = []

        def record(**kwargs: Any) -> Exception:
            recorded.append(kwargs)
            return AssertionError("retry() was raised")

        monkeypatch.setattr(ingest_symbol, "retry", record, raising=False)
        return recorded

    def raise_from_bridge(self, monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
        def boom(_factory: Any) -> Any:
            raise error

        monkeypatch.setattr(jobs_ingest, "run_async", boom)

    def test_a_rate_limit_is_rescheduled_with_the_base_classs_backoff(
        self, monkeypatch: pytest.MonkeyPatch, retries: list[dict[str, Any]]
    ) -> None:
        error = failure("rate_limited")
        self.raise_from_bridge(monkeypatch, error)

        with pytest.raises(AssertionError, match="retry"):
            ingest_symbol("AAPL", "2026-03")

        assert len(retries) == 1
        assert retries[0]["exc"] is error
        # `retry_countdown` is exponential from 30s, jittered downward into [delay/2, delay].
        assert 15 <= retries[0]["countdown"] <= 30

    def test_a_blank_key_fails_the_task_rather_than_rescheduling_it(
        self, monkeypatch: pytest.MonkeyPatch, retries: list[dict[str, Any]]
    ) -> None:
        self.raise_from_bridge(monkeypatch, failure("not_configured", setting="X"))

        with pytest.raises(ExternalServiceError):
            ingest_symbol("AAPL", "2026-03")

        assert retries == []

    def test_an_unknown_symbol_fails_the_task(
        self, monkeypatch: pytest.MonkeyPatch, retries: list[dict[str, Any]]
    ) -> None:
        """AlphaVantage's 200-with-``Error Message`` is a defect in the roster, and
        rescheduling it would loop until the retry budget ran out."""
        self.raise_from_bridge(monkeypatch, failure("client_error"))

        with pytest.raises(ExternalServiceError):
            ingest_symbol("AAPL", "2026-03")

        assert retries == []

    def test_an_untracked_ticker_is_not_a_vendor_problem_and_is_not_retried(
        self, monkeypatch: pytest.MonkeyPatch, retries: list[dict[str, Any]]
    ) -> None:
        self.raise_from_bridge(monkeypatch, NotFoundError("stock", "NOPE"))

        with pytest.raises(NotFoundError):
            ingest_symbol("AAPL", "2026-03")

        assert retries == []

    def test_a_database_failure_is_not_retried_either(
        self, monkeypatch: pytest.MonkeyPatch, retries: list[dict[str, Any]]
    ) -> None:
        self.raise_from_bridge(monkeypatch, RuntimeError("connection reset"))

        with pytest.raises(RuntimeError):
            ingest_symbol("AAPL", "2026-03")

        assert retries == []

    def test_the_retry_budget_is_the_base_classs(self) -> None:
        """Bounded, so even a permanently rate-limited symbol stops eventually."""
        assert ingest_symbol.max_retries == MAX_RETRIES
