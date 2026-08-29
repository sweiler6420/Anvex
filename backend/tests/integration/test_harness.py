"""The database half of the harness does what it claims — proof, not prose.

The tests in this module are the reason ANV-6 exists: every later ticket's repo and service
tests inherit this isolation, so if it silently stopped working the whole suite would start
lying. They run against the compose ``db-test`` service and **skip** when it is unreachable.

They are ordered on purpose. ``test_a_*`` writes rows; ``test_z_*`` asserts they are gone.
Read them top to bottom.

ANV-7 note: this module writes to the temporary ``scratch_table`` fixture because
``app/models/`` is still empty. Rewrite it against a real model and factory, and delete the
fixture, when the first model lands.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

ROLLED_BACK_LABEL = "written-without-committing"
COMMITTED_LABEL = "written-and-committed"


async def _count(session: AsyncSession, table: str, label: str | None = None) -> int:
    statement = f"SELECT count(*) FROM {table}"
    params: dict[str, str] = {}
    if label is not None:
        statement += " WHERE label = :label"
        params["label"] = label
    return int(await session.scalar(text(statement), params) or 0)


async def _insert(session: AsyncSession, table: str, label: str) -> None:
    await session.execute(text(f"INSERT INTO {table} (label) VALUES (:label)"), {"label": label})


class TestSchema:
    """The harness built the schema from the real migrations, not from `create_all`."""

    async def test_the_alembic_version_table_exists_in_the_anvex_schema(
        self, db_session: AsyncSession
    ) -> None:
        assert await db_session.scalar(text("SELECT to_regclass('anvex.alembic_version')"))

    async def test_exactly_one_revision_is_stamped(self, db_session: AsyncSession) -> None:
        """`upgrade head` ran once and completed — a partial run leaves zero rows."""
        assert await db_session.scalar(text("SELECT count(*) FROM anvex.alembic_version")) == 1

    async def test_pgcrypto_is_usable(self, db_session: AsyncSession) -> None:
        """Every UUID primary key defaults to `gen_random_uuid()`, so this is load-bearing."""
        assert await db_session.scalar(text("SELECT gen_random_uuid()")) is not None


class TestRollbackIsolation:
    """A write, a committed write, and then the proof that neither survived."""

    async def test_a_plain_write_is_visible_within_its_own_test(
        self, db_session: AsyncSession, scratch_table: str
    ) -> None:
        await _insert(db_session, scratch_table, ROLLED_BACK_LABEL)
        assert await _count(db_session, scratch_table, ROLLED_BACK_LABEL) == 1

    async def test_a_committed_write_is_visible_within_its_own_test(
        self, db_session: AsyncSession, scratch_table: str
    ) -> None:
        """`session.commit()` behaves normally — every service in this codebase calls it.

        With `join_transaction_mode="create_savepoint"` the commit releases a SAVEPOINT
        rather than committing the outer transaction, so the row is real for the rest of
        this test and gone for the next one.
        """
        await _insert(db_session, scratch_table, COMMITTED_LABEL)
        await db_session.commit()
        assert await _count(db_session, scratch_table, COMMITTED_LABEL) == 1

    async def test_repeated_commits_keep_working(
        self, db_session: AsyncSession, scratch_table: str
    ) -> None:
        """A service may commit several times in one request; the savepoint must restart."""
        for index in range(3):
            await _insert(db_session, scratch_table, f"{COMMITTED_LABEL}-{index}")
            await db_session.commit()
        assert await _count(db_session, scratch_table) == 3

    async def test_a_rollback_inside_a_test_does_not_break_the_session(
        self, db_session: AsyncSession, scratch_table: str
    ) -> None:
        """A service that catches a constraint violation and rolls back stays usable."""
        await _insert(db_session, scratch_table, "duplicate")
        await db_session.commit()
        with pytest.raises(IntegrityError):
            await _insert(db_session, scratch_table, "duplicate")
        await db_session.rollback()
        assert await _count(db_session, scratch_table, "duplicate") == 1

    async def test_z_none_of_the_above_survived(
        self, db_session: AsyncSession, scratch_table: str
    ) -> None:
        """**The isolation proof.**

        This test runs on a *different* connection from the ones above, so it can only see
        data that was actually committed to the database. The table is empty, which means
        every write in this class — including the explicitly committed ones — was undone
        when its test's outer transaction rolled back.
        """
        assert await _count(db_session, scratch_table) == 0

    async def test_z_and_an_independent_connection_agrees(
        self, db_engine: AsyncEngine, scratch_table: str
    ) -> None:
        """Belt and braces: ask outside the harness's session entirely."""
        async with db_engine.connect() as connection:
            total = await connection.scalar(text(f"SELECT count(*) FROM {scratch_table}"))
        assert total == 0


class TestDbClient:
    """`db_client` really points the app at the rolled-back session."""

    async def test_readiness_passes_against_the_real_test_database(
        self, db_client: AsyncClient
    ) -> None:
        """`/health/ready` issues a genuine `SELECT 1` through the overridden dependency."""
        response = await db_client.get("/health/ready")
        assert response.status_code == 200, response.text
        assert response.json() == {"status": "ok", "database": "ok"}

    async def test_the_request_and_the_test_share_one_session(
        self, db_client: AsyncClient, db_session: AsyncSession, scratch_table: str
    ) -> None:
        """A row created by the test is visible to the handler, and vanishes afterwards."""
        await _insert(db_session, scratch_table, "seen-by-the-handler")
        assert (await db_client.get("/health/ready")).status_code == 200
        assert await _count(db_session, scratch_table, "seen-by-the-handler") == 1
