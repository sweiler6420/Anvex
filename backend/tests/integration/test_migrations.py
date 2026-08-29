"""The migration history applies, reverses and re-applies against a real Postgres.

Lightweight on purpose — ANV-6 builds the real fixture harness. This test drives the
alembic *command* API exactly as a developer would from the shell, so it exercises the
async `env.py`, the `anvex` schema bootstrap and the `version_table_schema` wiring.

It **skips** when no database answers on the configured host, so the suite stays green on
a laptop with nothing running. To run it, point the standard Postgres settings at a live
server, e.g. against a throwaway container::

    docker run -d --name anvex-pg -e POSTGRES_PASSWORD=anvex -e POSTGRES_USER=anvex \
        -e POSTGRES_DB=anvex -p 55432:5432 postgres:16
    $env:POSTGRES_HOST = "localhost"; $env:POSTGRES_PORT = "55432"
    uv run python -m pytest tests/integration/test_migrations.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db.engine import create_engine
from app.settings import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


def alembic_config() -> Config:
    """The committed `alembic.ini`, with its relative script path made absolute."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_DIR / "app" / "db" / "migrations"))
    return config


async def _fetch_one(statement: str) -> object:
    engine = create_engine()
    try:
        async with engine.connect() as connection:
            return (await connection.execute(text(statement))).scalar_one()
    finally:
        await engine.dispose()


def require_database() -> None:
    """Skip the test unless the configured Postgres actually accepts a connection."""
    try:
        asyncio.run(_fetch_one("SELECT 1"))
    except Exception as exc:  # any connection failure is a skip, not a test failure
        settings = get_settings()
        pytest.skip(
            f"no Postgres at {settings.postgres_host}:{settings.postgres_port} "
            f"({type(exc).__name__}: {exc})"
        )


def test_migrations_upgrade_downgrade_and_upgrade_again() -> None:
    require_database()
    config = alembic_config()

    command.upgrade(config, "head")

    schema = get_settings().postgres_schema
    assert asyncio.run(
        _fetch_one(f"SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = '{schema}')")
    ), "the migration must create the anvex schema"
    assert asyncio.run(
        _fetch_one("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto')")
    ), "models rely on gen_random_uuid()"
    assert asyncio.run(
        _fetch_one(
            "SELECT EXISTS (SELECT 1 FROM pg_tables "
            f"WHERE schemaname = '{schema}' AND tablename = 'alembic_version')"
        )
    ), "alembic's version table must not land in public"
    # pgcrypto is usable, not merely installed.
    assert asyncio.run(_fetch_one("SELECT gen_random_uuid() IS NOT NULL"))

    command.downgrade(config, "base")
    command.upgrade(config, "head")
