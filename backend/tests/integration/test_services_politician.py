"""``PoliticianService`` against real Postgres, over the real roster.

``tests/unit/test_services_politician.py`` covers the branches against a fake; this module
covers the claims only a database can support, and there are three of them.

* **The filters really are exact in SQL**, so the service's normalisation is really what makes
  ``state="tx"`` work. The control is the point: the same query with the raw string is run
  through the repo directly and asserted to return nothing.
* **The seed is idempotent against a real ``ON CONFLICT``.** It is run twice over the
  checked-in file and the row count is asserted unchanged, with no ``UniqueViolation``
  escaping — which is the definition-of-done property for ANV-16 and cannot be proved against
  an in-memory dict.
* **The dedupe step is load-bearing, not decorative.** A batch with an internal duplicate is
  handed to the repo directly and asserted to raise ``cannot affect row a second time``, and
  then to the *service* and asserted to succeed. A fake could never show that the two differ.

The harness rolls every test back (``db_session``), so nothing here leaves rows behind, and
the module skips itself with Docker stopped rather than failing (``CLAUDE.md`` §6).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import NotFoundError
from app.models import Politician
from app.repos.politician import PoliticianRepo
from app.schemas.politician import PoliticianCreate
from app.services.politician import PoliticianService
from app.settings import Settings
from tests.factories import PoliticianFactory

# (id, last name, state, chamber, party)
ROSTER = (
    ("TX-SEN-R", "Ashgrove", "TX", "Senate", "Republican"),
    ("TX-HOU-R", "Blackwater", "TX", "House", "Republican"),
    ("TX-HOU-D", "Caldermill", "TX", "House", "Democrat"),
    ("CA-SEN-D", "Danforth", "CA", "Senate", "Democrat"),
    ("CA-HOU-D", "Ellsworth", "CA", "House", "Democrat"),
    ("VT-SEN-I", "Gainsborough", "VT", "Senate", "Independent"),
)


def build_service(session: AsyncSession) -> PoliticianService:
    """The real service over the real repo — only the session comes from the harness."""
    return PoliticianService(
        session,
        Settings(jwt_secret_key=SecretStr("integration-test-jwt-secret")),
        politicians=PoliticianRepo(),
    )


def seed_row(politician_id: str, **overrides: Any) -> PoliticianCreate:
    return PoliticianCreate.model_validate(
        {
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
    )


async def row_count(session: AsyncSession) -> int:
    """Every row in ``anvex.politicians``, counted in SQL rather than by the ORM."""
    return (await session.execute(select(func.count()).select_from(Politician))).scalar_one()


@pytest.fixture
async def roster(db_session: AsyncSession) -> None:
    for identifier, last_name, state, chamber, party in ROSTER:
        await PoliticianFactory().create(
            db_session,
            politician_id=identifier,
            last_name=last_name,
            state=state,
            chamber=chamber,
            party=party,
        )


# ---------------------------------------------------------------------------------------
# filters, against SQL rather than a dict comprehension
# ---------------------------------------------------------------------------------------


@pytest.mark.usefixtures("roster")
class TestFilters:
    @pytest.mark.parametrize(
        ("filters", "expected"),
        [
            ({"state": "TX"}, {"TX-SEN-R", "TX-HOU-R", "TX-HOU-D"}),
            ({"party": "Independent"}, {"VT-SEN-I"}),
            ({"chamber": "House"}, {"TX-HOU-R", "TX-HOU-D", "CA-HOU-D"}),
            ({"state": "TX", "party": "Republican"}, {"TX-SEN-R", "TX-HOU-R"}),
            ({"state": "TX", "party": "Republican", "chamber": "Senate"}, {"TX-SEN-R"}),
            ({"state": "WY"}, set()),
        ],
        ids=["state", "party", "chamber", "two", "all three", "nobody"],
    )
    async def test_the_filters_compose_with_and(
        self, db_session: AsyncSession, filters: dict[str, str], expected: set[str]
    ) -> None:
        page = await build_service(db_session).list_politicians(**filters)

        assert {item.politician_id for item in page.items} == expected
        assert page.total == len(expected)

    @pytest.mark.parametrize(
        "filters",
        [
            {"state": "tx"},
            {"state": " Tx "},
            {"state": "tx", "chamber": "senate"},
            {"state": "TX", "party": "republican", "chamber": "SENATE"},
        ],
    )
    async def test_a_lower_cased_filter_finds_the_same_people(
        self, db_session: AsyncSession, filters: dict[str, str]
    ) -> None:
        page = await build_service(db_session).list_politicians(**filters)

        assert page.total >= 1
        assert all(item.state == "TX" for item in page.items)

    async def test_the_repo_on_its_own_finds_nobody_for_the_same_input(
        self, db_session: AsyncSession
    ) -> None:
        """The control. The filter is a case-sensitive ``==`` in SQL, so the service's
        normalisation is the entire reason the test above passes."""
        rows, total = await PoliticianRepo().list_politicians(db_session, state="tx", limit=10)

        assert (rows, total) == ([], 0)

    async def test_the_roster_comes_back_ordered_by_surname(self, db_session: AsyncSession) -> None:
        page = await build_service(db_session).list_politicians()

        surnames = [item.last_name for item in page.items]
        assert surnames == sorted(surnames)

    async def test_two_identically_named_legislators_are_ordered_by_roster_id(
        self, db_session: AsyncSession
    ) -> None:
        """The third ordering key is what makes a page boundary between them stable —
        without it, ``LIMIT``/``OFFSET`` over an ambiguous order may repeat or skip a row."""
        for identifier in ("ZZ-2", "ZZ-1", "ZZ-3"):
            await PoliticianFactory().create(
                db_session,
                politician_id=identifier,
                last_name="Zzyzx",
                first_name="Adelaide",
            )

        page = await build_service(db_session).list_politicians()

        namesakes = [item.politician_id for item in page.items if item.last_name == "Zzyzx"]
        assert namesakes == ["ZZ-1", "ZZ-2", "ZZ-3"]

    async def test_paging_a_namesake_run_visits_each_of_them_exactly_once(
        self, db_session: AsyncSession
    ) -> None:
        """The reason the total order matters, exercised rather than asserted about."""
        for identifier in ("ZZ-2", "ZZ-1", "ZZ-3"):
            await PoliticianFactory().create(
                db_session,
                politician_id=identifier,
                last_name="Zzyzx",
                first_name="Adelaide",
            )
        service = build_service(db_session)
        seen: list[str] = []

        for offset in range(0, len(ROSTER) + 3, 1):
            page = await service.list_politicians(limit=1, offset=offset)
            seen.extend(item.politician_id for item in page.items)

        assert sorted(seen) == sorted(
            [identifier for identifier, _, _, _, _ in ROSTER] + ["ZZ-1", "ZZ-2", "ZZ-3"]
        )

    async def test_paging_over_the_whole_roster_visits_everybody_once(
        self, db_session: AsyncSession
    ) -> None:
        service = build_service(db_session)
        seen: list[str] = []

        for offset in range(0, len(ROSTER), 2):
            page = await service.list_politicians(limit=2, offset=offset)
            seen.extend(item.politician_id for item in page.items)

        assert sorted(seen) == sorted(identifier for identifier, _, _, _, _ in ROSTER)

    async def test_total_is_counted_before_the_window(self, db_session: AsyncSession) -> None:
        page = await build_service(db_session).list_politicians(limit=2, offset=500)

        assert page.items == []
        assert page.total == len(ROSTER)


@pytest.mark.usefixtures("roster")
class TestGetPolitician:
    async def test_a_known_roster_id_resolves(self, db_session: AsyncSession) -> None:
        result = await build_service(db_session).get_politician(politician_id="TX-SEN-R")

        assert result.last_name == "Ashgrove"

    async def test_an_unknown_roster_id_is_a_not_found(self, db_session: AsyncSession) -> None:
        with pytest.raises(NotFoundError) as caught:
            await build_service(db_session).get_politician(politician_id="ZZ-000")

        assert caught.value.details == {"resource": "politician", "identifier": "ZZ-000"}

    async def test_a_read_leaves_the_roster_alone(self, db_session: AsyncSession) -> None:
        before = await row_count(db_session)

        await build_service(db_session).list_politicians()

        assert await row_count(db_session) == before


# ---------------------------------------------------------------------------------------
# the seed — the property the ticket exists for
# ---------------------------------------------------------------------------------------


class TestTheSeedIsIdempotent:
    async def test_seeding_the_checked_in_roster_fills_the_table(
        self, db_session: AsyncSession
    ) -> None:
        report = await build_service(db_session).seed_roster()

        assert report.loaded > 20
        assert report.written == report.loaded
        assert await row_count(db_session) == report.written

    async def test_running_it_twice_leaves_the_row_count_unchanged(
        self, db_session: AsyncSession
    ) -> None:
        """The definition-of-done property, against a real ``ON CONFLICT DO UPDATE``.

        No ``UniqueViolation`` escapes and nothing is duplicated: the second run updates the
        same fifty-odd rows to the values they already hold.
        """
        service = build_service(db_session)

        first = await service.seed_roster()
        after_first = await row_count(db_session)
        second = await service.seed_roster()
        after_second = await row_count(db_session)

        assert after_first == after_second == first.loaded
        assert first.written == second.written

    async def test_running_it_three_times_is_no_different(self, db_session: AsyncSession) -> None:
        service = build_service(db_session)

        await service.seed_roster()
        await service.seed_roster()
        report = await service.seed_roster()

        assert await row_count(db_session) == report.loaded

    async def test_a_second_run_refreshes_a_row_that_changed(
        self, db_session: AsyncSession
    ) -> None:
        """A legislator changing chamber is the case the upsert exists for."""
        service = build_service(db_session)
        await service.seed_roster(rows=[seed_row("P-1", chamber="House", party="Democrat")])

        await service.seed_roster(rows=[seed_row("P-1", chamber="Senate", party="Republican")])

        db_session.expire_all()
        stored = await service.get_politician(politician_id="P-1")
        assert (stored.chamber, stored.party) == ("Senate", "Republican")
        assert await row_count(db_session) == 1

    async def test_seeding_over_an_existing_roster_does_not_delete_anybody(
        self, db_session: AsyncSession
    ) -> None:
        """No delete-then-insert: the table is never briefly empty and unrelated rows stay."""
        await PoliticianFactory().create(db_session, politician_id="LEGACY-1")

        await build_service(db_session).seed_roster(rows=[seed_row("P-1")])

        assert await build_service(db_session).get_politician(politician_id="LEGACY-1")

    async def test_the_seed_commits(self, db_session: AsyncSession) -> None:
        """Committed inside the harness's savepoint, so the rows are visible and still
        rolled back at teardown."""
        await build_service(db_session).seed_roster(rows=[seed_row("P-1")])

        assert await row_count(db_session) == 1


class TestTheDedupeStepIsLoadBearing:
    """A batch with an internal duplicate: what the repo does, and what the service does."""

    async def test_the_repo_alone_is_rejected_by_postgres(self, db_session: AsyncSession) -> None:
        rows = [seed_row("P-1").model_dump(), seed_row("P-1").model_dump()]

        with pytest.raises(DBAPIError, match="cannot affect row a second time"):
            await PoliticianRepo().bulk_upsert(db_session, rows)

        # Postgres aborts the transaction on the error; the harness's outer transaction is
        # rolled back at teardown either way, but the savepoint has to be released here or
        # every later statement in this test would be refused.
        await db_session.rollback()

    async def test_the_service_deduplicates_and_succeeds(self, db_session: AsyncSession) -> None:
        """Same batch, one layer up. This is the difference the dedupe step makes."""
        report = await build_service(db_session).seed_roster(
            rows=[seed_row("P-1", party="Democrat"), seed_row("P-1", party="Republican")]
        )

        assert (report.loaded, report.written, report.duplicates) == (2, 1, ("P-1",))
        assert await row_count(db_session) == 1

    async def test_the_last_occurrence_is_what_landed(self, db_session: AsyncSession) -> None:
        await build_service(db_session).seed_roster(
            rows=[seed_row("P-1", party="Democrat"), seed_row("P-1", party="Republican")]
        )

        stored = await build_service(db_session).get_politician(politician_id="P-1")
        assert stored.party == "Republican"

    async def test_ids_differing_only_in_case_are_two_people(
        self, db_session: AsyncSession
    ) -> None:
        """The primary key is case-sensitive, so the dedupe must not fold either."""
        report = await build_service(db_session).seed_roster(
            rows=[seed_row("P-1"), seed_row("p-1")]
        )

        assert report.written == 2
        assert await row_count(db_session) == 2
