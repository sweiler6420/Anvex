"""``PoliticianRepo`` against a real Postgres.

Reference data, so the two things worth proving are that the filters compose (a client
asks for "Republican senators from Texas" in one request) and that re-seeding the roster
refreshes rows instead of duplicating them.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.repos import PoliticianRepo
from tests.factories import PoliticianFactory

repo = PoliticianRepo()


def _row(politician_id: str, **overrides: Any) -> dict[str, Any]:
    """A complete roster row for :meth:`PoliticianRepo.bulk_upsert`."""
    return {
        "politician_id": politician_id,
        "first_name": "Pat",
        "last_name": "Roster",
        "party": "Independent",
        "state": "CA",
        "chamber": "House",
        "dob": dt.date(1970, 1, 1),
        "gender": "F",
        **overrides,
    }


async def _seed(session: AsyncSession) -> None:
    """A roster with every filter combination represented."""
    await PoliticianFactory().create(
        session,
        politician_id="TX-SEN-R",
        last_name="Adams",
        state="TX",
        party="Republican",
        chamber="Senate",
    )
    await PoliticianFactory().create(
        session,
        politician_id="TX-HOU-R",
        last_name="Baker",
        state="TX",
        party="Republican",
        chamber="House",
    )
    await PoliticianFactory().create(
        session,
        politician_id="TX-SEN-D",
        last_name="Carter",
        state="TX",
        party="Democrat",
        chamber="Senate",
    )
    await PoliticianFactory().create(
        session,
        politician_id="CA-SEN-D",
        last_name="Diaz",
        state="CA",
        party="Democrat",
        chamber="Senate",
    )


class TestLookups:
    async def test_get_by_id(self, db_session: AsyncSession) -> None:
        politician = await PoliticianFactory().create(db_session)

        found = await repo.get_by_id(db_session, politician.politician_id)

        assert found is not None
        assert found.politician_id == politician.politician_id

    async def test_get_by_id_is_none_for_an_unknown_id(self, db_session: AsyncSession) -> None:
        assert await repo.get_by_id(db_session, "not-on-the-roster") is None

    async def test_nullable_columns_come_back_as_none(self, db_session: AsyncSession) -> None:
        """A presidential-level entry has no state; historical rows often lack a dob."""
        await PoliticianFactory().create(
            db_session, politician_id="P-NULLS", state=None, chamber=None, dob=None, gender=None
        )

        found = await repo.get_by_id(db_session, "P-NULLS")

        assert found is not None
        assert (found.state, found.chamber, found.dob, found.gender) == (None, None, None, None)


class TestListing:
    async def test_it_lists_the_roster_by_surname(self, db_session: AsyncSession) -> None:
        await _seed(db_session)

        rows, total = await repo.list_politicians(db_session, limit=10)

        assert [p.last_name for p in rows] == ["Adams", "Baker", "Carter", "Diaz"]
        assert total == 4

    async def test_each_filter_narrows_the_roster(self, db_session: AsyncSession) -> None:
        await _seed(db_session)

        _, by_state = await repo.list_politicians(db_session, state="TX", limit=10)
        _, by_party = await repo.list_politicians(db_session, party="Democrat", limit=10)
        _, by_chamber = await repo.list_politicians(db_session, chamber="Senate", limit=10)

        assert (by_state, by_party, by_chamber) == (3, 2, 3)

    async def test_the_filters_combine(self, db_session: AsyncSession) -> None:
        """ "Republican senators from Texas" is one call, not three round trips."""
        await _seed(db_session)

        rows, total = await repo.list_politicians(
            db_session, state="TX", party="Republican", chamber="Senate", limit=10
        )

        assert total == 1
        assert rows[0].politician_id == "TX-SEN-R"

    async def test_a_filter_that_matches_nothing_is_empty(self, db_session: AsyncSession) -> None:
        await _seed(db_session)

        assert await repo.list_politicians(db_session, state="ZZ", limit=10) == ([], 0)

    async def test_filters_are_exact_matches(self, db_session: AsyncSession) -> None:
        """These are enumerated values; normalising a query parameter is the service's job."""
        await _seed(db_session)

        assert await repo.list_politicians(db_session, state="tx", limit=10) == ([], 0)


class TestPaginationBoundaries:
    async def test_limit_windows_the_rows_but_not_the_total(self, db_session: AsyncSession) -> None:
        await _seed(db_session)

        rows, total = await repo.list_politicians(db_session, limit=2)

        assert [p.last_name for p in rows] == ["Adams", "Baker"]
        assert total == 4

    async def test_offset_continues_the_ordering(self, db_session: AsyncSession) -> None:
        await _seed(db_session)

        rows, total = await repo.list_politicians(db_session, limit=2, offset=2)

        assert [p.last_name for p in rows] == ["Carter", "Diaz"]
        assert total == 4

    async def test_an_offset_past_the_end_still_reports_the_total(
        self, db_session: AsyncSession
    ) -> None:
        await _seed(db_session)

        rows, total = await repo.list_politicians(db_session, limit=10, offset=40)

        assert rows == []
        assert total == 4

    async def test_the_total_counts_the_filter_not_the_window(
        self, db_session: AsyncSession
    ) -> None:
        await _seed(db_session)

        rows, total = await repo.list_politicians(db_session, state="TX", limit=1)

        assert len(rows) == 1
        assert total == 3


class TestWrites:
    async def test_create_persists_a_legislator(self, db_session: AsyncSession) -> None:
        created = await repo.create(db_session, **_row("P-NEW"))

        assert created.politician_id == "P-NEW"
        assert await repo.get_by_id(db_session, "P-NEW") is not None

    async def test_bulk_upsert_inserts_a_roster(self, db_session: AsyncSession) -> None:
        written = await repo.bulk_upsert(db_session, [_row("P-1"), _row("P-2"), _row("P-3")])

        assert written == 3
        assert (await repo.list_politicians(db_session, limit=10))[1] == 3

    async def test_running_the_seed_twice_updates_rather_than_duplicating(
        self, db_session: AsyncSession
    ) -> None:
        """The property ANV-16's re-runnable seed rests on."""
        rows = [_row("P-1"), _row("P-2")]

        first = await repo.bulk_upsert(db_session, rows)
        after_first = (await repo.list_politicians(db_session, limit=10))[1]

        second = await repo.bulk_upsert(db_session, rows)
        after_second = (await repo.list_politicians(db_session, limit=10))[1]

        assert (first, second) == (2, 2)
        assert after_first == after_second == 2

    async def test_a_re_seed_refreshes_changed_fields(self, db_session: AsyncSession) -> None:
        """A legislator switches chamber; the roster row is updated in place."""
        await repo.bulk_upsert(db_session, [_row("P-1", chamber="House", party="Democrat")])

        await repo.bulk_upsert(db_session, [_row("P-1", chamber="Senate", party="Republican")])

        db_session.expire_all()
        stored = await repo.get_by_id(db_session, "P-1")
        assert stored is not None
        assert (stored.chamber, stored.party) == ("Senate", "Republican")
        assert (await repo.list_politicians(db_session, limit=10))[1] == 1

    async def test_an_empty_batch_is_a_no_op(self, db_session: AsyncSession) -> None:
        assert await repo.bulk_upsert(db_session, []) == 0

    async def test_a_batch_with_internal_duplicates_is_rejected(
        self, db_session: AsyncSession
    ) -> None:
        """Same caller obligation as the stock-data upsert: deduplicate first."""
        with pytest.raises(DBAPIError, match="cannot affect row a second time"):
            await repo.bulk_upsert(db_session, [_row("P-1"), _row("P-1")])

    async def test_bulk_upsert_does_not_commit(self, db_session: AsyncSession) -> None:
        await repo.bulk_upsert(db_session, [_row("P-1")])

        await db_session.rollback()

        assert await repo.get_by_id(db_session, "P-1") is None

    async def test_delete_removes_a_legislator(self, db_session: AsyncSession) -> None:
        politician = await PoliticianFactory().create(db_session)

        await repo.delete(db_session, politician)

        assert await repo.get_by_id(db_session, politician.politician_id) is None
