"""Queries over the ``users`` table.

Everything ANV-11 (auth) and ANV-12 (users) need to find, create and uniqueness-check an
account — and nothing else. There is no password hashing here, no "is this login valid",
no token construction: this module answers *which row*, and the service decides what that
means (``CLAUDE.md`` §3).
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import User, Watchlist, WatchlistData
from app.repos.base import BaseRepo


class UserRepo(BaseRepo[User]):
    """Data access for :class:`app.models.User`."""

    model = User

    # -----------------------------------------------------------------------------------
    # Lookups
    # -----------------------------------------------------------------------------------

    async def get_by_id(self, session: AsyncSession, user_id: uuid.UUID) -> User | None:
        """The account with this id, or ``None``."""
        return await self._one_or_none(session, select(User).where(User.user_id == user_id))

    async def get_by_email(self, session: AsyncSession, email: str) -> User | None:
        """The account with this email address, or ``None``."""
        return await self._one_or_none(session, select(User).where(User.email == email))

    async def get_by_username(self, session: AsyncSession, username: str) -> User | None:
        """The account with this username, or ``None``."""
        return await self._one_or_none(session, select(User).where(User.username == username))

    async def get_by_email_or_username(self, session: AsyncSession, identifier: str) -> User | None:
        """**The login lookup.** One query matching ``identifier`` against *either* column.

        The old API's ``/v1/login`` accepted an email address or a username in
        ``OAuth2PasswordRequestForm.username`` and resolved both with a single ``OR``
        (``routers/auth.py``). That behaviour is preserved exactly, and deliberately stays
        one statement rather than two sequential lookups: two round trips would be slower
        and — because the second only runs when the first misses — would leak which of the
        two an unknown identifier failed on through response timing.

        Both columns are unique, so at most one row can match either arm.
        """
        return await self._one_or_none(
            session,
            select(User).where(or_(User.email == identifier, User.username == identifier)),
        )

    async def get_with_watchlists(self, session: AsyncSession, user_id: uuid.UUID) -> User | None:
        """The account with every watchlist, entry and stock eagerly loaded.

        The full ``user -> watchlists -> entries -> stock`` chain from
        ``tests/integration/test_models.py::TestRelationships``. Lazy loading raises under
        asyncio, so a caller that intends to walk the graph must ask for this rather than
        :meth:`get_by_id`. Entry order comes from ``Watchlist.entries``'
        ``order_by=position``; no caller re-sorts.
        """
        return await self._one_or_none(
            session,
            select(User)
            .where(User.user_id == user_id)
            .options(
                selectinload(User.watchlists)
                .selectinload(Watchlist.entries)
                .selectinload(WatchlistData.stock)
            ),
        )

    # -----------------------------------------------------------------------------------
    # Uniqueness
    # -----------------------------------------------------------------------------------

    async def email_exists(
        self,
        session: AsyncSession,
        email: str,
        *,
        exclude_user_id: uuid.UUID | None = None,
    ) -> bool:
        """Whether any account already uses this email address.

        ``exclude_user_id`` is what makes the same check usable on an update: "is this
        address taken *by somebody else*". Without it, saving a profile without changing
        the email would report a conflict with itself.
        """
        stmt = select(User.user_id).where(User.email == email)
        if exclude_user_id is not None:
            stmt = stmt.where(User.user_id != exclude_user_id)
        return await self._exists(session, stmt)

    async def username_exists(
        self,
        session: AsyncSession,
        username: str,
        *,
        exclude_user_id: uuid.UUID | None = None,
    ) -> bool:
        """Whether any account already uses this username. See :meth:`email_exists`."""
        stmt = select(User.user_id).where(User.username == username)
        if exclude_user_id is not None:
            stmt = stmt.where(User.user_id != exclude_user_id)
        return await self._exists(session, stmt)

    # -----------------------------------------------------------------------------------
    # Writes
    # -----------------------------------------------------------------------------------

    async def create(
        self,
        session: AsyncSession,
        *,
        username: str,
        email: str,
        password: str,
    ) -> User:
        """Insert an account and flush, so ``user_id`` and ``created_at`` are readable.

        ``password`` is the **hash** — the column keeps its legacy name and this repo does
        no hashing (ANV-10 owns that, ANV-12 calls it). Passing a plaintext here would
        store a plaintext; the type system cannot tell, so the service must not.
        """
        return await self.add(session, User(username=username, email=email, password=password))


#: A stateless, shareable instance. Repos hold no session, so one is enough.
user_repo = UserRepo()

__all__ = ["UserRepo", "user_repo"]
