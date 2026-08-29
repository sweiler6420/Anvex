"""Alembic environment — fully async, schema-aware.

Three things make this different from the stock template:

1. **The URL comes from ``app.settings``**, not from ``alembic.ini``. The repo-root
   ``.env`` is the only place the database is configured (``CLAUDE.md`` §2).
2. **It is async end to end** (``async_engine_from_config`` + ``connection.run_sync``),
   because the backend has exactly one driver — ``asyncpg`` (``CLAUDE.md`` §2/§4) — and a
   second, blocking one would be a second thing to install, configure and keep working.
3. **Everything lives in the ``anvex`` schema**, including ``alembic_version``. Because
   alembic creates that table *before* the first migration runs, the schema is bootstrapped
   here with an idempotent ``CREATE SCHEMA IF NOT EXISTS``; the first revision then
   declares the same thing so a fresh database is fully described by the migration history.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Importing the models package registers every table on `Base.metadata` so that
# `--autogenerate` sees them. It is empty until ANV-7 — imported anyway so that adding
# models never requires touching this file.
import app.models  # noqa: F401
from app.db.base import Base
from app.settings import get_settings

config = context.config

if config.config_file_name is not None:
    # `disable_existing_loggers=False` keeps pytest's and the app's loggers alive when
    # migrations are driven from inside a test process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

settings = get_settings()
SCHEMA: str = settings.postgres_schema

# `%` is the configparser interpolation character, and a Postgres password may contain one.
config.set_main_option("sqlalchemy.url", settings.postgres_dsn.replace("%", "%%"))

target_metadata = Base.metadata


def include_name(name: str | None, type_: str, parent_names: dict[str, Any]) -> bool:
    """Limit reflection to the ``anvex`` schema.

    ``include_schemas=True`` otherwise makes autogenerate reflect *every* schema in the
    database — ``public``, and anything an extension created — and then propose dropping
    the tables it finds there.
    """
    if type_ == "schema":
        return name == SCHEMA
    return True


def _configure(**kwargs: Any) -> None:
    context.configure(
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=include_name,
        # Keep alembic's bookkeeping table out of `public`.
        version_table_schema=SCHEMA,
        compare_type=True,
        compare_server_default=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a DBAPI connection (``alembic upgrade head --sql``)."""
    _configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run the migrations on an open (sync-facade) connection."""
    # Must precede `context.configure`: alembic creates `anvex.alembic_version` as soon as
    # migrations start, and CREATE TABLE fails if the schema does not exist yet.
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
    # That execute autobegins a transaction, and committing it here is not optional:
    # leaving it open turns alembic's own `begin_transaction()` into a no-op nested block,
    # so nothing is committed and every migration silently rolls back on disconnect.
    connection.commit()

    _configure(connection=connection)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open an async engine and hand a connection to the sync migration runner."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
