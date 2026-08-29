"""The database half of the harness does what it claims — proof, not prose.

The tests in this module are the reason ANV-6 exists: every later ticket's repo and service
tests inherit this isolation, so if it silently stopped working the whole suite would start
lying. They run against the compose ``db-test`` service and **skip** when it is unreachable.

They are ordered on purpose. ``test_a_*`` writes rows; ``test_z_*`` asserts they are gone.
Read them top to bottom.

Since ANV-7 the writes go through a real model and its factory rather than a throwaway
scaffold table (the ``scratch_table`` fixture is gone). That matters twice over: the table
now comes from the migrations the harness runs, so the isolation proof and the schema under
test are the same thing, and the factory gets exercised on every run.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models import User
from tests.factories import UserFactory

#: `users` is the stand-in for "any real table": it is created by ANV-7's migration, it
#: outlives every test's transaction, and it has a unique column to violate.
COUNT_USERS = select(func.count()).select_from(User.__table__)


async def _user_count(session: AsyncSession) -> int:
    return int(await session.scalar(COUNT_USERS) or 0)


class TestSchema:
    """The harness built the schema from the real migrations, not from `create_all`."""

    async def test_the_alembic_version_table_exists_in_the_anvex_schema(
        self, db_session: AsyncSession
    ) -> None:
        assert await db_session.scalar(text("SELECT to_regclass('anvex.alembic_version')"))

    async def test_exactly_one_revision_is_stamped(self, db_session: AsyncSession) -> None:
        """`upgrade head` ran to completion — a partial run leaves zero rows."""
        assert await db_session.scalar(text("SELECT count(*) FROM anvex.alembic_version")) == 1

    async def test_pgcrypto_is_usable(self, db_session: AsyncSession) -> None:
        """Every UUID primary key defaults to `gen_random_uuid()`, so this is load-bearing."""
        assert await db_session.scalar(text("SELECT gen_random_uuid()")) is not None

    async def test_the_model_tables_exist(self, db_session: AsyncSession) -> None:
        """`upgrade head` reached ANV-7's revision, not merely the bootstrap."""
        assert await _user_count(db_session) == 0


class TestRollbackIsolation:
    """A write, a committed write, and then the proof that neither survived."""

    async def test_a_plain_write_is_visible_within_its_own_test(
        self, db_session: AsyncSession
    ) -> None:
        await UserFactory().create(db_session)
        assert await _user_count(db_session) == 1

    async def test_a_committed_write_is_visible_within_its_own_test(
        self, db_session: AsyncSession
    ) -> None:
        """`session.commit()` behaves normally — every service in this codebase calls it.

        With `join_transaction_mode="create_savepoint"` the commit releases a SAVEPOINT
        rather than committing the outer transaction, so the row is real for the rest of
        this test and gone for the next one.
        """
        await UserFactory().create(db_session)
        await db_session.commit()
        assert await _user_count(db_session) == 1

    async def test_repeated_commits_keep_working(self, db_session: AsyncSession) -> None:
        """A service may commit several times in one request; the savepoint must restart."""
        for _ in range(3):
            await UserFactory().create(db_session)
            await db_session.commit()
        assert await _user_count(db_session) == 3

    async def test_a_rollback_inside_a_test_does_not_break_the_session(
        self, db_session: AsyncSession
    ) -> None:
        """A service that catches a constraint violation and rolls back stays usable."""
        user = await UserFactory().create(db_session)
        await db_session.commit()
        with pytest.raises(IntegrityError):
            await UserFactory().create(db_session, email=user.email)
        await db_session.rollback()
        assert await _user_count(db_session) == 1

    async def test_z_none_of_the_above_survived(self, db_session: AsyncSession) -> None:
        """**The isolation proof.**

        This test runs on a *different* connection from the ones above, so it can only see
        data that was actually committed to the database. The table is empty, which means
        every write in this class — including the explicitly committed ones — was undone
        when its test's outer transaction rolled back.
        """
        assert await _user_count(db_session) == 0

    async def test_z_and_an_independent_connection_agrees(self, db_engine: AsyncEngine) -> None:
        """Belt and braces: ask outside the harness's session entirely."""
        async with db_engine.connect() as connection:
            assert await connection.scalar(COUNT_USERS) == 0


class TestFactoriesAgainstTheDatabase:
    """The two factory rules (`CLAUDE.md` §6) hold against real constraints, not in theory."""

    async def test_a_factory_flushes_so_server_defaults_are_populated(
        self, db_session: AsyncSession
    ) -> None:
        user = await UserFactory().create(db_session)
        assert user.user_id is not None, "flush() must have returned the gen_random_uuid()"
        assert user.created_at is not None

    async def test_a_factory_does_not_commit(self, db_session: AsyncSession) -> None:
        """After `create()` the work is still in flight — the caller owns the boundary."""
        await UserFactory().create(db_session)
        assert db_session.in_transaction()

    async def test_sequence_derived_columns_survive_a_unique_constraint(
        self, db_session: AsyncSession
    ) -> None:
        """Twenty users in one test — the exact case `fake.email()` would fail."""
        users = await UserFactory().create_many(db_session, 20)
        assert len({user.email for user in users}) == 20
        assert await _user_count(db_session) == 20


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
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A row created by the test is visible to the handler, and vanishes afterwards."""
        await UserFactory().create(db_session)
        assert (await db_client.get("/health/ready")).status_code == 200
        assert await _user_count(db_session) == 1
