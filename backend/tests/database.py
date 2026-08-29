"""How the test suite reaches — and prepares — the ``db-test`` Postgres.

Everything database-shaped that is *not* a fixture lives here, so ``conftest.py`` stays a
list of fixtures rather than a mix of fixtures and plumbing.

**Why the test DSN is built here and not in ``app/settings.py``.** ``CLAUDE.md`` §4 says of
the two Postgres services: *"``db-test`` is the only database a test may write to … Nothing
in ``app/`` ever knows it exists."* Adding ``postgres_test_*`` fields to :class:`Settings`
would break that in the very commit that documents it, and it would ship a test-only
concern into the production container's config surface. So the harness owns a **separate,
test-only** ``BaseSettings`` class instead. It reads the same repo-root ``.env``
(``CLAUDE.md`` §2 — still one file), and borrows the user/password from the real
:class:`Settings` because ``db-test`` genuinely shares those credentials with ``db``.

**Why the defaults are host-shaped.** ``POSTGRES_HOST``/``POSTGRES_PORT`` default to the
*in-network* ``db:5432`` because the app always runs in a container. pytest does not: the
normal workflow is ``uv run python -m pytest`` on the host against the published
``localhost:5433``. So the test host/port default to the host side, and running the suite
*inside* the compose network is the case that overrides them
(``POSTGRES_TEST_HOST=db-test POSTGRES_TEST_PORT=5432``).

**Never 5432.** A natively installed ``postgresql-x64-18`` service owns that port on this
machine, and on Windows both it and Docker's proxy bind it successfully — a host client
then silently reaches the wrong server with no error at all. The published test port is
5433 and the default here matches it.
"""

from __future__ import annotations

import asyncio
from functools import cache
from pathlib import Path
from urllib.parse import quote_plus

from alembic import command
from alembic.config import Config
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.settings import BACKEND_DIR, ENV_FILE, get_settings

#: The migration tree the harness runs. Note that the committed ``alembic.ini`` is
#: deliberately *not* loaded (see :func:`alembic_config`).
MIGRATIONS_DIR: Path = BACKEND_DIR / "app" / "db" / "migrations"

#: Seconds to wait for the test server to answer before declaring it unreachable. Short on
#: purpose: with the container stopped this is dead time on every developer's fast run.
PROBE_TIMEOUT_SECONDS: float = 3.0

#: Maintenance database used only for ``CREATE DATABASE`` / ``DROP DATABASE``.
MAINTENANCE_DATABASE: str = "postgres"


class HarnessDatabaseSettings(BaseSettings):
    """Test-only view of the ``db-test`` connection details.

    Not named ``Test…`` on purpose — pytest would try to collect it as a test class.
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    #: ``localhost`` from the host; set to ``db-test`` when running inside the network.
    postgres_test_host: str = "localhost"
    #: Falls back to the compose *publication* port so there is only one number to change:
    #: editing ``POSTGRES_TEST_HOST_PORT`` moves both the mapping and the client.
    #: ``POSTGRES_TEST_PORT`` exists for the in-network case, where they differ (5432).
    postgres_test_port: int = Field(
        default=5433,
        validation_alias=AliasChoices("POSTGRES_TEST_PORT", "POSTGRES_TEST_HOST_PORT"),
    )
    #: Same key compose feeds to the container as its ``POSTGRES_DB``.
    postgres_test_db: str = "anvex_test"


@cache
def harness_settings() -> HarnessDatabaseSettings:
    """The harness's database settings, read once per process."""
    return HarnessDatabaseSettings()


def database_url(database: str | None = None) -> str:
    """Async DSN for ``db-test``, optionally overriding the database name.

    ``database`` is used by the throwaway-database fixtures; leave it ``None`` for the
    shared ``anvex_test``.
    """
    app_settings = get_settings()
    harness = harness_settings()
    user = quote_plus(app_settings.postgres_user)
    password = quote_plus(app_settings.postgres_password.get_secret_value())
    host = f"{harness.postgres_test_host}:{harness.postgres_test_port}"
    return f"postgresql+asyncpg://{user}:{password}@{host}/{database or harness.postgres_test_db}"


def describe_target() -> str:
    """Human-readable ``host:port/database``, for skip messages."""
    harness = harness_settings()
    return f"{harness.postgres_test_host}:{harness.postgres_test_port}/{harness.postgres_test_db}"


async def _ping(url: str) -> None:
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"timeout": PROBE_TIMEOUT_SECONDS},
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


@cache
def unavailable_reason() -> str | None:
    """``None`` when ``db-test`` answers, otherwise the reason to skip.

    Cached: probing once per session keeps the "Docker is stopped" path cheap, and the
    answer cannot change usefully mid-run anyway.

    **Any** failure is a skip, never an error. ``CLAUDE.md`` §6 requires the default suite
    to be green with the daemon stopped, and a test that fails because a developer has no
    container running teaches them to ignore failures.
    """
    try:
        asyncio.run(_ping(database_url()))
    except Exception as exc:  # every connection failure is equally a skip
        return (
            f"no test Postgres at {describe_target()} ({type(exc).__name__}: {exc}). "
            "Start it with `docker compose up -d db-test`."
        )
    return None


def alembic_config(url: str) -> Config:
    """An Alembic config pointed at ``url``, built **without** reading ``alembic.ini``.

    Two reasons to skip the ini file:

    * ``env.py`` calls ``fileConfig()`` whenever ``config_file_name`` is set, which
      replaces the root logger's handlers — including the one pytest's ``caplog`` installs.
      Migrating inside the test process would then silently break log assertions in any
      test that happens to run afterwards.
    * Nothing else in the ini matters for ``upgrade``: ``file_template`` and the ruff
      post-write hook only apply to ``revision``.

    The URL is handed over through ``config.attributes``, which ``env.py`` prefers over
    ``settings.postgres_dsn`` when present. That is the only supported way to migrate a
    database other than the configured one, and it avoids mutating the process environment
    and clearing the ``get_settings`` cache — which would repoint the *application* engine
    for the rest of the session.
    """
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["sqlalchemy.url"] = url
    return config


def upgrade_to_head(url: str) -> None:
    """Apply every migration to ``url``.

    Synchronous by design: ``env.py`` owns its own ``asyncio.run``, so this is safe to call
    from a plain (non-async) session-scoped fixture.
    """
    command.upgrade(alembic_config(url), "head")


async def _run_maintenance(statement: str) -> None:
    """Execute one statement against the maintenance database, outside a transaction."""
    engine = create_async_engine(
        database_url(MAINTENANCE_DATABASE),
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",  # CREATE/DROP DATABASE cannot run in a transaction
        connect_args={"timeout": PROBE_TIMEOUT_SECONDS},
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text(statement))
    finally:
        await engine.dispose()


def create_database(name: str) -> None:
    """Create a throwaway database on the test server."""
    asyncio.run(_run_maintenance(f'CREATE DATABASE "{name}"'))


def drop_database(name: str) -> None:
    """Drop a throwaway database, evicting any connection still attached to it."""
    asyncio.run(_run_maintenance(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


__all__ = [
    "MIGRATIONS_DIR",
    "HarnessDatabaseSettings",
    "alembic_config",
    "create_database",
    "database_url",
    "describe_target",
    "drop_database",
    "harness_settings",
    "unavailable_reason",
    "upgrade_to_head",
]
