"""Queries over the ``politicians`` roster.

Reference data with a natural primary key (``politician_id``, the roster's own external
identifier) and no owner, no timestamps and no user writes. It is read as a filtered list
and written only by ANV-16's seed, which is why the two things here are a filtered,
paginated list and an idempotent bulk upsert.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Politician
from app.repos.base import BaseRepo

#: Everything a re-seed may overwrite: every column except the natural key it matches on.
UPDATABLE_COLUMNS = (
    "state",
    "chamber",
    "dob",
    "first_name",
    "last_name",
    "gender",
    "party",
)


class PoliticianRepo(BaseRepo[Politician]):
    """Data access for :class:`app.models.Politician`."""

    model = Politician

    async def get_by_id(
        self, session: AsyncSession, politician_id: str
    ) -> Politician | None:
        """The legislator with this roster id, or ``None``."""
        return await self._one_or_none(
            session, select(Politician).where(Politician.politician_id == politician_id)
        )

    async def list_politicians(
        self,
        session: AsyncSession,
        *,
        state: str | None = None,
        party: str | None = None,
        chamber: str | None = None,
        limit: int,
        offset: int = 0,
    ) -> tuple[list[Politician], int]:
        """One window of the roster plus the total matching count.

        The three filters are exact matches and combine with ``AND`` — "Republican
        senators from Texas" is one call. Each is independently optional, so no filter,
        one, two or all three are the same method.

        Matching is exact rather than case-insensitive because these are enumerated
        values, not free text: a state is a two-letter code and a chamber is one of a
        handful of strings the seed writes. Normalising a query parameter to the roster's
        casing is the service's job.

        Ordered by surname, then forename, then the roster id — the id is what makes the
        order total, so a page boundary cannot land in the middle of two identically-named
        legislators and repeat or skip one.
        """
        stmt = select(Politician)
        if state is not None:
            stmt = stmt.where(Politician.state == state)
        if party is not None:
            stmt = stmt.where(Politician.party == party)
        if chamber is not None:
            stmt = stmt.where(Politician.chamber == chamber)
        stmt = stmt.order_by(
            Politician.last_name.asc(),
            Politician.first_name.asc(),
            Politician.politician_id.asc(),
        )
        return await self._page(session, stmt, limit=limit, offset=offset)

    async def create(self, session: AsyncSession, **values: Any) -> Politician:
        """Insert one legislator and flush.

        Keyword arguments rather than a fixed signature: the roster has eight columns, four
        of them nullable, and ANV-16 already validates the shape with ``PoliticianCreate``
        before it gets here.
        """
        return await self.add(session, Politician(**values))

    async def bulk_upsert(
        self, session: AsyncSession, rows: Iterable[Mapping[str, Any]]
    ) -> int:
        """Insert or update many legislators in one statement; returns rows written.

        ``INSERT ... ON CONFLICT (politician_id) DO UPDATE`` — the same idempotency
        property as :meth:`app.repos.stock_data.StockDataRepo.bulk_upsert`, keyed on the
        natural primary key rather than a unique constraint. Re-running ANV-16's seed
        refreshes the roster (a legislator changes chamber, a party affiliation flips)
        without duplicating anybody and without a delete-then-insert that would briefly
        empty the table.

        As there, the caller deduplicates ``rows`` first: Postgres rejects a statement that
        hits the same conflict target twice.
        """
        values = list(rows)
        if not values:
            return 0

        stmt = pg_insert(Politician).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Politician.politician_id],
            set_={column: getattr(stmt.excluded, column) for column in UPDATABLE_COLUMNS},
        ).returning(Politician.politician_id)

        result = await session.execute(stmt)
        return len(result.scalars().all())


#: A stateless, shareable instance. Repos hold no session, so one is enough.
politician_repo = PoliticianRepo()

__all__ = ["UPDATABLE_COLUMNS", "PoliticianRepo", "politician_repo"]
