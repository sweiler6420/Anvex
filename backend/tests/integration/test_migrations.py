"""The migration history applies, reverses and re-applies against a real Postgres.

It runs against a **throwaway database** created on the ``db-test`` server and dropped
afterwards, not against ``anvex_test`` itself: ``downgrade base`` would otherwise tear the
schema out from under the session-scoped ``db_engine`` and break every other integration
test depending on ordering.

This drives the alembic *command* API much as a developer would from the shell, so it
exercises the async ``env.py``, the ``anvex`` schema bootstrap and the
``version_table_schema`` wiring. It **skips** when ``db-test`` is unreachable, like every
other database test.

Run it with::

    docker compose up -d db-test
    uv run python -m pytest tests/integration/test_migrations.py
"""

from __future__ import annotations

import asyncio

from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.settings import get_settings
from tests import database

SCHEMA = get_settings().postgres_schema


def fetch_one(url: str, statement: str) -> object:
    """Run one scalar query against ``url``, opening and closing its own engine."""

    async def _run() -> object:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                return (await connection.execute(text(statement))).scalar_one()
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def test_migrations_upgrade_downgrade_and_upgrade_again(throwaway_database_url: str) -> None:
    url = throwaway_database_url
    config = database.alembic_config(url)

    command.upgrade(config, "head")

    assert fetch_one(
        url, f"SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = '{SCHEMA}')"
    ), "the migration must create the anvex schema"
    assert fetch_one(
        url, "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto')"
    ), "models rely on gen_random_uuid()"
    assert fetch_one(
        url,
        "SELECT EXISTS (SELECT 1 FROM pg_tables "
        f"WHERE schemaname = '{SCHEMA}' AND tablename = 'alembic_version')",
    ), "alembic's version table must not land in public"
    # pgcrypto is usable, not merely installed.
    assert fetch_one(url, "SELECT gen_random_uuid() IS NOT NULL")

    command.downgrade(config, "base")
    stamped = fetch_one(url, f"SELECT count(*) FROM {SCHEMA}.alembic_version")
    assert stamped == 0, "downgrade must clear the stamped revision, not merely run the SQL"

    command.upgrade(config, "head")
    assert fetch_one(url, f"SELECT count(*) FROM {SCHEMA}.alembic_version") == 1


def test_the_harness_can_migrate_a_database_other_than_the_configured_one(
    throwaway_database_url: str,
) -> None:
    """The ``config.attributes["sqlalchemy.url"]`` hook in ``env.py`` really is honoured.

    Without it the harness would have to mutate ``POSTGRES_*`` in the environment and clear
    the ``get_settings`` cache, repointing the *application* engine for the rest of the
    session. The database migrated below is one that no setting names, so if the hook were
    ignored this would either fail or — worse — quietly migrate something else.
    """
    command.upgrade(database.alembic_config(throwaway_database_url), "head")

    assert fetch_one(throwaway_database_url, f"SELECT count(*) FROM {SCHEMA}.alembic_version") == 1
