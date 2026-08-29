"""``UserRepo`` against a real Postgres.

The login lookup is the test that matters most here: the old API let a person sign in with
*either* their email address or their username, and losing that in the rewrite would lock
out half the existing accounts without a single test failing anywhere else.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.repos import UserRepo
from tests.factories import StockFactory, UserFactory, WatchlistDataFactory, WatchlistFactory

repo = UserRepo()


class TestLookups:
    async def test_get_by_id_returns_the_account(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)

        found = await repo.get_by_id(db_session, user.user_id)

        assert found is not None
        assert found.user_id == user.user_id

    async def test_get_by_id_returns_none_for_an_unknown_id(
        self, db_session: AsyncSession
    ) -> None:
        assert await repo.get_by_id(db_session, uuid.uuid4()) is None

    async def test_get_by_email_and_get_by_username(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)

        by_email = await repo.get_by_email(db_session, user.email)
        by_username = await repo.get_by_username(db_session, user.username)

        assert by_email is not None and by_email.user_id == user.user_id
        assert by_username is not None and by_username.user_id == user.user_id

    async def test_get_by_email_and_username_return_none_for_misses(
        self, db_session: AsyncSession
    ) -> None:
        await UserFactory().create(db_session)

        assert await repo.get_by_email(db_session, "nobody@example.com") is None
        assert await repo.get_by_username(db_session, "nobody") is None


class TestLoginLookup:
    """`get_by_email_or_username` — one call, both columns (the old `/v1/login`)."""

    async def test_it_matches_on_email(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)

        found = await repo.get_by_email_or_username(db_session, user.email)

        assert found is not None
        assert found.user_id == user.user_id

    async def test_it_matches_on_username_with_the_same_call(
        self, db_session: AsyncSession
    ) -> None:
        user = await UserFactory().create(db_session)

        found = await repo.get_by_email_or_username(db_session, user.username)

        assert found is not None
        assert found.user_id == user.user_id

    async def test_both_identifiers_resolve_to_the_same_row(
        self, db_session: AsyncSession
    ) -> None:
        """The behaviour preserved from the old API, stated as one assertion."""
        user = await UserFactory().create(db_session)

        by_email = await repo.get_by_email_or_username(db_session, user.email)
        by_username = await repo.get_by_email_or_username(db_session, user.username)

        assert by_email is not None
        assert by_username is not None
        assert by_email.user_id == by_username.user_id == user.user_id

    async def test_it_does_not_match_another_account(self, db_session: AsyncSession) -> None:
        """One user's username must never resolve to another user's row."""
        first = await UserFactory().create(db_session)
        second = await UserFactory().create(db_session)

        found = await repo.get_by_email_or_username(db_session, second.username)

        assert found is not None
        assert found.user_id == second.user_id
        assert found.user_id != first.user_id

    async def test_an_unknown_identifier_is_none(self, db_session: AsyncSession) -> None:
        await UserFactory().create(db_session)

        assert await repo.get_by_email_or_username(db_session, "who@example.com") is None
        assert await repo.get_by_email_or_username(db_session, "who") is None

    async def test_it_is_case_sensitive(self, db_session: AsyncSession) -> None:
        """No folding here: normalising an identifier is a rule, and rules are not in repos."""
        user = await UserFactory().create(db_session)

        assert await repo.get_by_email_or_username(db_session, user.email.upper()) is None


class TestEagerLoading:
    async def test_get_with_watchlists_loads_the_whole_graph(
        self, db_session: AsyncSession
    ) -> None:
        """Lazy loading raises under asyncio, so this must come back fully populated."""
        user = await UserFactory().create(db_session)
        stock = await StockFactory().create(db_session, ticker_symbol="NVDA")
        watchlist = await WatchlistFactory().create(db_session, user=user, title="Semis")
        await WatchlistDataFactory().create(
            db_session, watchlist=watchlist, stock=stock, position=0
        )
        db_session.expunge_all()

        found = await repo.get_with_watchlists(db_session, user.user_id)

        assert found is not None
        assert [w.title for w in found.watchlists] == ["Semis"]
        assert [e.stock.ticker_symbol for e in found.watchlists[0].entries] == ["NVDA"]

    async def test_get_with_watchlists_is_none_for_an_unknown_user(
        self, db_session: AsyncSession
    ) -> None:
        assert await repo.get_with_watchlists(db_session, uuid.uuid4()) is None

    async def test_a_user_with_no_watchlists_loads_an_empty_list(
        self, db_session: AsyncSession
    ) -> None:
        user = await UserFactory().create(db_session)
        db_session.expunge_all()

        found = await repo.get_with_watchlists(db_session, user.user_id)

        assert found is not None
        assert found.watchlists == []


class TestUniqueness:
    async def test_email_exists_detects_a_duplicate(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)

        assert await repo.email_exists(db_session, user.email) is True
        assert await repo.email_exists(db_session, "free@example.com") is False

    async def test_username_exists_detects_a_duplicate(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)

        assert await repo.username_exists(db_session, user.username) is True
        assert await repo.username_exists(db_session, "unclaimed") is False

    async def test_excluding_the_owner_makes_the_check_usable_on_an_update(
        self, db_session: AsyncSession
    ) -> None:
        """"Taken by somebody else" — otherwise a profile save conflicts with itself."""
        user = await UserFactory().create(db_session)

        assert (
            await repo.email_exists(db_session, user.email, exclude_user_id=user.user_id) is False
        )
        assert (
            await repo.username_exists(db_session, user.username, exclude_user_id=user.user_id)
            is False
        )

    async def test_excluding_a_different_user_still_reports_the_conflict(
        self, db_session: AsyncSession
    ) -> None:
        taken = await UserFactory().create(db_session)
        other = await UserFactory().create(db_session)

        assert (
            await repo.email_exists(db_session, taken.email, exclude_user_id=other.user_id) is True
        )


class TestWrites:
    async def test_create_persists_and_populates_server_defaults(
        self, db_session: AsyncSession
    ) -> None:
        user = await repo.create(
            db_session,
            username="newcomer",
            email="newcomer@example.com",
            password="$2b$12$notarealhash",
        )

        assert isinstance(user.user_id, uuid.UUID)
        assert user.created_at.tzinfo is not None
        assert await repo.get_by_email(db_session, "newcomer@example.com") is not None

    async def test_create_does_not_commit(self, db_session: AsyncSession) -> None:
        """Rolling back must undo it — proof the repo only flushed (`CLAUDE.md` §3)."""
        await repo.create(
            db_session, username="rollback", email="rollback@example.com", password="hash"
        )

        await db_session.rollback()

        assert await repo.get_by_email(db_session, "rollback@example.com") is None

    async def test_a_duplicate_email_raises_at_the_flush(self, db_session: AsyncSession) -> None:
        """The uniqueness check is for a clean 409; the constraint is what guarantees it."""
        existing = await UserFactory().create(db_session)

        with pytest.raises(IntegrityError, match="uq_users_email"):
            await repo.create(
                db_session, username="different", email=existing.email, password="hash"
            )

    async def test_update_applies_only_the_keys_it_is_given(
        self, db_session: AsyncSession
    ) -> None:
        user = await UserFactory().create(db_session)
        original_email = user.email

        await repo.update(db_session, user, {"username": "renamed"})

        assert user.username == "renamed"
        assert user.email == original_email

    async def test_delete_removes_the_account(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)

        await repo.delete(db_session, user)

        assert await repo.get_by_id(db_session, user.user_id) is None
