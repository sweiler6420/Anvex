"""Unit tests for `app.db`.

Everything here is plumbing: an engine is built, inspected and thrown away, and a session
is created and closed. **No connection is ever opened** — SQLAlchemy does no I/O until a
connection is actually requested, which is what keeps these tests in `tests/unit/`.
Migrations against a real database are covered by `tests/integration/test_migrations.py`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db import engine as db_engine
from app.db import session as db_session
from app.db.base import NAMING_CONVENTION, SCHEMA, Base, metadata
from app.settings import Settings, get_settings

# Deliberately unlike the real `.env` so an assertion cannot pass by coincidence.
TEST_ENV = {
    "POSTGRES_USER": "tester",
    "POSTGRES_PASSWORD": "s3cr3t",
    "POSTGRES_HOST": "db.example",
    "POSTGRES_PORT": "6543",
    "POSTGRES_DB": "anvex_test",
}


@pytest.fixture(autouse=True)
def _isolated_db_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin the settings and reset the module-level singletons around every test."""
    for name, value in TEST_ENV.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    monkeypatch.setattr(db_engine, "_engine", None)
    monkeypatch.setattr(db_session, "_sessionmaker", None)
    monkeypatch.setattr(db_session, "_bound_engine", None)
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------- engine


def test_engine_url_comes_from_settings() -> None:
    engine = db_engine.create_engine()

    assert isinstance(engine, AsyncEngine)
    assert engine.url.render_as_string(hide_password=False) == get_settings().postgres_dsn
    assert engine.url.drivername == "postgresql+asyncpg"
    assert engine.url.host == "db.example"
    assert engine.url.port == 6543
    assert engine.url.database == "anvex_test"
    assert engine.url.username == "tester"


def test_engine_accepts_explicitly_passed_settings() -> None:
    settings = Settings(_env_file=None, postgres_host="somewhere-else", postgres_port=15432)

    engine = db_engine.create_engine(settings)

    assert engine.url.host == "somewhere-else"
    assert engine.url.port == 15432


def test_engine_pool_is_configured() -> None:
    engine = db_engine.create_engine()
    pool = engine.pool

    assert pool._pre_ping is True, "a dead connection must not surface as a random 500"
    assert pool.size() == db_engine.POOL_SIZE
    assert pool._max_overflow == db_engine.MAX_OVERFLOW
    assert pool._timeout == db_engine.POOL_TIMEOUT_SECONDS
    assert pool._recycle == db_engine.POOL_RECYCLE_SECONDS


def test_get_engine_returns_one_engine_per_process() -> None:
    assert db_engine.get_engine() is db_engine.get_engine()


async def test_dispose_engine_releases_and_forgets_the_engine() -> None:
    first = db_engine.get_engine()

    await db_engine.dispose_engine()

    assert db_engine._engine is None
    assert db_engine.get_engine() is not first


async def test_dispose_engine_is_safe_when_nothing_was_created() -> None:
    await db_engine.dispose_engine()  # must not raise
    assert db_engine._engine is None


# --------------------------------------------------------------------------- sessions


def test_sessionmaker_produces_async_sessions() -> None:
    factory = db_session.get_sessionmaker()

    assert isinstance(factory, async_sessionmaker)
    assert isinstance(factory(), AsyncSession)


def test_sessionmaker_does_not_expire_on_commit() -> None:
    """Services commit and then hand models to a response schema; expiring would re-query."""
    assert db_session.get_sessionmaker().kw["expire_on_commit"] is False


def test_sessionmaker_is_cached_and_bound_to_the_current_engine() -> None:
    factory = db_session.get_sessionmaker()
    assert db_session.get_sessionmaker() is factory
    assert factory.kw["bind"] is db_engine.get_engine()

    db_engine._engine = None  # simulate a dispose

    rebuilt = db_session.get_sessionmaker()
    assert rebuilt is not factory
    assert rebuilt.kw["bind"] is db_engine.get_engine()


async def test_get_session_yields_a_session_and_closes_it() -> None:
    closed: list[str] = []

    async with db_session.get_session() as session:
        assert isinstance(session, AsyncSession)
        _spy_on_close(session, closed)

    assert closed == ["closed"]


async def test_get_session_rolls_back_and_closes_when_the_body_raises() -> None:
    closed: list[str] = []

    with pytest.raises(RuntimeError, match="boom"):
        async with db_session.get_session() as session:
            _spy_on_close(session, closed)
            raise RuntimeError("boom")

    assert closed == ["rolled back", "closed"]


def _spy_on_close(session: AsyncSession, log: list[str]) -> None:
    """Record `rollback()`/`close()` on one session without touching the database."""
    original_rollback, original_close = session.rollback, session.close

    async def rollback() -> None:
        log.append("rolled back")
        await original_rollback()

    async def close() -> None:
        log.append("closed")
        await original_close()

    session.rollback = rollback  # type: ignore[method-assign]
    session.close = close  # type: ignore[method-assign]


# ------------------------------------------------------------------------------ base


def test_metadata_is_bound_to_the_anvex_schema() -> None:
    assert SCHEMA == "anvex"
    assert metadata.schema == "anvex"
    assert Base.metadata is metadata
    assert Base.metadata.schema == "anvex"


def test_metadata_carries_the_constraint_naming_convention() -> None:
    convention = Base.metadata.naming_convention

    assert set(convention) == {"ix", "uq", "ck", "fk", "pk"}
    assert convention == NAMING_CONVENTION
    assert convention["pk"] == "pk_%(table_name)s"
    assert "%(referred_table_name)s" in convention["fk"]
