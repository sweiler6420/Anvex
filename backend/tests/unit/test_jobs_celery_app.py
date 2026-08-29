"""Unit tests for ``app.jobs.celery_app`` — configuration, registration and the schedule.

Celery's configuration is a flat namespace of about two hundred keys with defaults for all
of them, which makes a wrong value indistinguishable from an unset one by inspection. The
handful that Anvex actually *decided* are asserted here, so a later edit that "tidies up"
one of them fails a test that says why it existed.

Two of these are sweeps rather than single assertions, on ANV-15's argument: a property that
must hold for *every* task or *every* schedule entry is better expressed as one derived
check than as N hand-written ones, the last of which will be forgotten.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from celery import Celery
from celery.signals import setup_logging, worker_process_init

import app.jobs.celery_app as jobs_module
from app.db import engine as db_engine
from app.jobs.base import AnvexTask
from app.jobs.celery_app import (
    BEAT_SCHEDULE,
    BROKER_VISIBILITY_TIMEOUT_SECONDS,
    RESULT_EXPIRES,
    TASK_MODULES,
    TASK_SOFT_TIME_LIMIT_SECONDS,
    TASK_TIME_LIMIT_SECONDS,
    celery_app,
    create_celery_app,
)
from app.settings import Settings

#: `app/jobs/` modules that hold no tasks. Everything else in the package must be imported
#: by the worker, or its tasks are unroutable.
NON_TASK_MODULES = frozenset({"__init__", "base", "celery_app"})

#: Names Celery registers on every app for its own housekeeping.
BUILTIN_PREFIX = "celery."


@pytest.fixture(autouse=True)
def _tasks_imported() -> Iterator[None]:
    """Import every task module, so the registry under test is the one a worker would see."""
    celery_app.loader.import_default_modules()
    yield


def anvex_task_names() -> list[str]:
    """Every task Anvex registered, excluding Celery's own built-ins."""
    return sorted(name for name in celery_app.tasks if not name.startswith(BUILTIN_PREFIX))


# ------------------------------------------------------------------------------- transport


def test_broker_and_backend_come_from_settings() -> None:
    """Values unlike the real `.env`, so the assertion cannot pass by coincidence."""
    settings = Settings(
        _env_file=None,
        celery_broker_url="redis://elsewhere:6380/7",
        celery_result_backend="redis://elsewhere:6380/8",
    )

    app = create_celery_app(settings)

    assert app.conf.broker_url == "redis://elsewhere:6380/7"
    assert app.conf.result_backend == "redis://elsewhere:6380/8"


def test_the_module_level_app_is_a_celery_app_using_the_anvex_task_base() -> None:
    assert isinstance(celery_app, Celery)
    assert issubclass(celery_app.Task, AnvexTask)


def test_startup_survives_a_broker_that_is_not_up_yet() -> None:
    """Redis is a second or two behind the worker in the compose graph; that is not a crash."""
    assert celery_app.conf.broker_connection_retry_on_startup is True


def test_the_visibility_timeout_exceeds_the_hard_time_limit() -> None:
    """Otherwise a slow task is redelivered to a *second* worker while the first still runs.

    Redis has no real acknowledgement — ``kombu`` re-delivers anything not finished within
    ``visibility_timeout``. Concurrent duplicate execution is not something idempotency makes
    free, and the failure is invisible until a job gets slow, which is why it is asserted.
    """
    configured = celery_app.conf.broker_transport_options["visibility_timeout"]

    assert configured == BROKER_VISIBILITY_TIMEOUT_SECONDS
    assert configured > TASK_TIME_LIMIT_SECONDS


def test_the_soft_limit_leaves_room_to_clean_up_before_the_hard_one() -> None:
    assert TASK_SOFT_TIME_LIMIT_SECONDS < TASK_TIME_LIMIT_SECONDS
    assert celery_app.conf.task_soft_time_limit == TASK_SOFT_TIME_LIMIT_SECONDS
    assert celery_app.conf.task_time_limit == TASK_TIME_LIMIT_SECONDS


# -------------------------------------------------------------------------- serialisation


def test_json_only_in_both_directions() -> None:
    """`pickle` would execute arbitrary code from anything that can write to Redis."""
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.result_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]
    assert celery_app.conf.result_accept_content == ["json"]


def test_results_expire() -> None:
    assert celery_app.conf.result_expires == RESULT_EXPIRES
    assert RESULT_EXPIRES.total_seconds() == timedelta(hours=24).total_seconds()


def test_scheduling_is_utc() -> None:
    assert celery_app.conf.timezone == "UTC"
    assert celery_app.conf.enable_utc is True


# ---------------------------------------------------------------------- acknowledgement


def test_acks_late_so_a_lost_connection_redelivers() -> None:
    assert celery_app.conf.task_acks_late is True


def test_a_lost_worker_process_does_not_redeliver() -> None:
    """The deliberately asymmetric half of the pair.

    A lost *connection* is the network's fault and redelivery is free. A lost *process* is
    very often the message's fault — the task that OOMed will OOM again — and redelivering
    it kills every worker that picks it up, with no natural end. Lose the run, keep the
    workers; beat re-drives the job on its next tick because the job is idempotent.
    """
    assert celery_app.conf.task_reject_on_worker_lost is False


def test_prefetch_is_one_because_acks_are_late() -> None:
    """A larger prefetch means a restart redelivers a *batch* of half-run long tasks."""
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_a_started_task_is_distinguishable_from_a_queued_one() -> None:
    assert celery_app.conf.task_track_started is True


def test_celery_does_not_hijack_the_root_logger() -> None:
    """`app/middleware/logging.py` owns it; two configurations means two log formats."""
    assert celery_app.conf.worker_hijack_root_logger is False
    assert celery_app.conf.worker_redirect_stdouts is False


# ------------------------------------------------------------------------- registration


def test_ping_is_registered() -> None:
    assert "jobs.health.ping" in celery_app.tasks


def test_every_task_module_on_disk_is_imported_by_the_worker() -> None:
    """The sweep that stops a new job being written and then never running.

    A task in a module the worker does not import is not a bug anyone sees: the task simply
    never runs, and a caller gets ``NotRegistered``. Deriving the expected list from the
    directory means adding ``app/jobs/ingest.py`` fails here until it is registered.
    """
    on_disk = {
        path.stem
        for path in (Path(jobs_module.__file__).parent).glob("*.py")
        if path.stem not in NON_TASK_MODULES
    }

    assert {name.rsplit(".", 1)[-1] for name in TASK_MODULES} == on_disk
    assert all(name.startswith("app.jobs.") for name in TASK_MODULES)
    assert celery_app.conf.imports == TASK_MODULES


def test_every_task_name_is_explicit_and_namespaced() -> None:
    """Celery's default name is the import path, so a file rename orphans queued messages.

    Every Anvex task passes ``name="jobs.<module>.<function>"``; this sweep is what makes
    that a rule rather than a habit.
    """
    names = anvex_task_names()

    assert names, "no Anvex tasks are registered — the sweep would pass vacuously"
    for name in names:
        assert name.startswith("jobs."), name
        assert not name.startswith("anvex."), (
            f"{name} looks auto-generated from the module path; pass an explicit name="
        )


# ----------------------------------------------------------------- worker boot and fork


def test_importing_the_task_modules_opens_no_database_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker parent must reach the ``fork`` with no engine.

    A prefork worker forks *after* importing the task modules, so an engine built at import
    time would be inherited by every child along with its sockets. The engine is lazy, which
    makes this true today; asserting it is what keeps a future module-level
    ``get_engine()`` from making it quietly false.
    """
    monkeypatch.setattr(db_engine, "_engine", None)

    celery_app.loader.import_default_modules()

    assert db_engine.current_engine() is None


def test_worker_process_init_discards_an_inherited_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The child-side guard, tested through the signal rather than by calling the handler.

    What matters is the *wiring*: an unconnected handler is indistinguishable from a
    connected one by reading it, and only the signal proves which this is.
    """
    monkeypatch.setattr(db_engine, "_engine", None)
    db_engine.get_engine()

    worker_process_init.send(sender=None)

    assert db_engine.current_engine() is None


def test_the_worker_gets_the_applications_logging_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connecting to ``setup_logging`` at all is what tells Celery to leave logging alone."""
    calls: list[object] = []
    monkeypatch.setattr(jobs_module, "configure_logging", lambda settings: calls.append(settings))

    setup_logging.send(sender=None)

    assert len(calls) == 1


# ----------------------------------------------------------------------- beat schedule


def test_the_schedule_is_wired_into_the_app() -> None:
    assert celery_app.conf.beat_schedule == BEAT_SCHEDULE


def test_the_schedule_has_a_heartbeat_entry() -> None:
    assert BEAT_SCHEDULE["health-ping"]["task"] == "jobs.health.ping"
    assert BEAT_SCHEDULE["health-ping"]["schedule"] == timedelta(minutes=5)


@pytest.mark.parametrize("slug", sorted(BEAT_SCHEDULE))
def test_every_schedule_entry_names_a_registered_task(slug: str) -> None:
    """A beat entry naming a task nobody registered fails once a tick, forever, in the log."""
    entry = BEAT_SCHEDULE[slug]

    assert entry["task"] in celery_app.tasks


@pytest.mark.parametrize("slug", sorted(BEAT_SCHEDULE))
def test_every_schedule_entry_expires_before_its_next_tick(slug: str) -> None:
    """Beat publishes whether or not anything is consuming.

    With the workers down, a queue collects one message per tick; without an expiry,
    bringing them back replays hours of stale ticks at once. An expiry shorter than the
    interval means at most one pending run of any job — the next tick *is* the retry.
    """
    entry = BEAT_SCHEDULE[slug]
    schedule = entry["schedule"]

    assert isinstance(schedule, timedelta), (
        f"{slug} uses a crontab; assert its expiry against the crontab's own period"
    )
    expires = entry["options"]["expires"]
    assert 0 < expires < schedule.total_seconds()
