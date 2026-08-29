"""Unit tests for ``app/services/politician.py`` against an in-memory repo.

The unit tier (``CLAUDE.md`` §6): the real service, :class:`tests.helpers.FakePoliticianRepo`
underneath it, no Postgres and no skip. The fake is deliberately unhelpful in the two ways
the real repo is — its filters are exact and case-sensitive, and its ``bulk_upsert`` raises
on an internal duplicate exactly as Postgres does — so a service that forgot to normalise, or
forgot to deduplicate, fails *here* rather than in production.

Three things this module exists to pin.

**What reached the repo, not just what came back.** Every normalisation test asserts on
``repo.calls``, because "the right rows came back" is also true of a service that passed the
raw string to a fake that happened to be forgiving. The boundary is the claim.

**Idempotency is the service's, not only the database's.** ``TestSeedRoster`` runs the seed
twice against the same fake and asserts the row count is unchanged, and separately asserts
that the batch handed to ``bulk_upsert`` never contains a repeated id — which is the
precondition Postgres enforces and the reason the dedupe step exists at all.

**The sweep.** ``TestEveryUseCase`` is parameterised over a case list *derived* from
``vars(PoliticianService)`` and asserted complete, so a use case added without coverage fails
the suite. Its property is the one that must hold for all of them: a read never commits, and
the one writer always does. That is ANV-15's pattern applied to the property this service
actually has — there is no ownership to sweep, because reference data has no owner.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import SecretStr

from app.domain.errors import NotFoundError
from app.schemas.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from app.schemas.politician import PoliticianCreate
from app.services.politician import PoliticianService, SeedReport
from app.settings import Settings
from tests.helpers import FakePoliticianRepo, StubSession, make_politician

# (id, last name, state, chamber, party) — enough combinations that every filter and every
# pair of filters selects a different, non-empty subset.
ROSTER = (
    ("TX-SEN-R", "Ashgrove", "TX", "Senate", "Republican"),
    ("TX-HOU-R", "Blackwater", "TX", "House", "Republican"),
    ("TX-HOU-D", "Caldermill", "TX", "House", "Democrat"),
    ("CA-SEN-D", "Danforth", "CA", "Senate", "Democrat"),
    ("CA-HOU-D", "Ellsworth", "CA", "House", "Democrat"),
    ("NY-SEN-D", "Fairbank", "NY", "Senate", "Democrat"),
    ("VT-SEN-I", "Gainsborough", "VT", "Senate", "Independent"),
)


@dataclass
class World:
    """The service, the fake it sits on, and the stub session that counts commits."""

    service: PoliticianService
    politicians: FakePoliticianRepo
    session: StubSession


@pytest.fixture
def settings() -> Settings:
    return Settings(jwt_secret_key=SecretStr("unit-tier-jwt-secret"))


@pytest.fixture
def politicians() -> FakePoliticianRepo:
    return FakePoliticianRepo(
        *(
            make_politician(
                politician_id=identifier,
                last_name=last_name,
                state=state,
                chamber=chamber,
                party=party,
            )
            for identifier, last_name, state, chamber, party in ROSTER
        )
    )


@pytest.fixture
def world(settings: Settings, politicians: FakePoliticianRepo) -> World:
    session = StubSession()
    service = PoliticianService(session, settings, politicians=politicians)  # type: ignore[arg-type]
    return World(service=service, politicians=politicians, session=session)


def seed_row(politician_id: str, **overrides: Any) -> PoliticianCreate:
    return PoliticianCreate.model_validate(
        {
            "politician_id": politician_id,
            "first_name": "Adelaide",
            "last_name": "Ashgrove",
            "party": "Democrat",
            "state": "CA",
            "chamber": "Senate",
            "dob": dt.date(1960, 5, 4),
            "gender": "F",
            **overrides,
        }
    )


# ---------------------------------------------------------------------------------------
# list_politicians
# ---------------------------------------------------------------------------------------


class TestListPoliticians:
    async def test_no_filters_is_the_whole_roster_in_an_envelope(self, world: World) -> None:
        page = await world.service.list_politicians()

        assert page.total == len(ROSTER)
        assert len(page.items) == len(ROSTER)
        assert (page.limit, page.offset) == (DEFAULT_PAGE_LIMIT, 0)
        assert page.has_more is False

    async def test_items_are_the_public_shape_ordered_by_surname(self, world: World) -> None:
        page = await world.service.list_politicians()

        assert [item.last_name for item in page.items] == sorted(
            last_name for _, last_name, _, _, _ in ROSTER
        )
        assert set(page.items[0].model_dump()) == {
            "politician_id",
            "first_name",
            "last_name",
            "party",
            "state",
            "chamber",
            "dob",
            "gender",
        }

    async def test_no_orm_row_escapes_the_service(self, world: World) -> None:
        page = await world.service.list_politicians()

        assert all(type(item).__name__ == "PoliticianOut" for item in page.items)

    @pytest.mark.parametrize(
        ("filters", "expected"),
        [
            ({"state": "TX"}, {"TX-SEN-R", "TX-HOU-R", "TX-HOU-D"}),
            ({"party": "Independent"}, {"VT-SEN-I"}),
            ({"chamber": "Senate"}, {"TX-SEN-R", "CA-SEN-D", "NY-SEN-D", "VT-SEN-I"}),
            ({"state": "TX", "party": "Republican"}, {"TX-SEN-R", "TX-HOU-R"}),
            ({"state": "TX", "chamber": "House"}, {"TX-HOU-R", "TX-HOU-D"}),
            (
                {"state": "TX", "party": "Republican", "chamber": "Senate"},
                {"TX-SEN-R"},
            ),
            ({"state": "TX", "party": "Independent"}, set()),
        ],
        ids=[
            "state",
            "party",
            "chamber",
            "state+party",
            "state+chamber",
            "all three",
            "no match",
        ],
    )
    async def test_the_filters_combine_with_and(
        self, world: World, filters: dict[str, str], expected: set[str]
    ) -> None:
        page = await world.service.list_politicians(**filters)

        assert {item.politician_id for item in page.items} == expected
        assert page.total == len(expected)

    @pytest.mark.parametrize(
        ("given", "canonical"),
        [
            ({"state": "tx"}, {"state": "TX"}),
            ({"state": " Tx "}, {"state": "TX"}),
            ({"party": "republican"}, {"party": "Republican"}),
            ({"party": "REPUBLICAN"}, {"party": "Republican"}),
            ({"chamber": "senate"}, {"chamber": "Senate"}),
            ({"chamber": " HOUSE "}, {"chamber": "House"}),
        ],
    )
    async def test_a_filter_is_normalised_before_it_reaches_the_repo(
        self, world: World, given: dict[str, str], canonical: dict[str, str]
    ) -> None:
        """The repo matches exactly, so this is the difference between a page and nothing.

        Asserted at the boundary: a fake that folded case would let a service with no
        normalisation pass the result assertion below.
        """
        await world.service.list_politicians(**given)

        method, arguments = world.politicians.calls[-1]
        assert method == "list_politicians"
        for field, expected in canonical.items():
            assert arguments[field] == expected

    @pytest.mark.parametrize("given", ["tx", " TX ", "Tx"])
    async def test_any_casing_of_a_state_finds_the_same_people(
        self, world: World, given: str
    ) -> None:
        page = await world.service.list_politicians(state=given)

        assert page.total == 3

    @pytest.mark.parametrize("field", ["state", "party", "chamber"])
    async def test_a_blank_filter_is_no_filter_at_all(self, world: World, field: str) -> None:
        """``?state=`` must mean "everybody", not "nobody" — the repo would match ``''``."""
        page = await world.service.list_politicians(**{field: "   "})

        assert page.total == len(ROSTER)
        assert world.politicians.calls[-1][1][field] is None

    async def test_an_unknown_filter_value_is_an_empty_page_not_an_error(
        self, world: World
    ) -> None:
        page = await world.service.list_politicians(party="Whig")

        assert page.items == []
        assert page.total == 0

    async def test_the_window_is_resolved_by_the_shared_rule(self, world: World) -> None:
        """``resolve_window`` is ANV-14's, moved down to ``app/domain/pagination.py`` when
        this service became its third caller."""
        await world.service.list_politicians(limit=None, offset=-5)

        assert world.politicians.calls[-1][1]["limit"] == DEFAULT_PAGE_LIMIT
        assert world.politicians.calls[-1][1]["offset"] == 0

    async def test_an_over_large_limit_is_clamped_for_a_caller_with_no_request(
        self, world: World
    ) -> None:
        await world.service.list_politicians(limit=10_000)

        assert world.politicians.calls[-1][1]["limit"] == MAX_PAGE_LIMIT

    async def test_limit_and_offset_move_the_window(self, world: World) -> None:
        page = await world.service.list_politicians(limit=2, offset=2)

        assert [item.last_name for item in page.items] == ["Caldermill", "Danforth"]
        assert (page.limit, page.offset, page.total) == (2, 2, len(ROSTER))
        assert page.has_more is True

    async def test_an_offset_past_the_end_keeps_the_total_truthful(self, world: World) -> None:
        page = await world.service.list_politicians(offset=500)

        assert page.items == []
        assert page.total == len(ROSTER)
        assert page.has_more is False

    async def test_the_echoed_window_is_the_resolved_one(self, world: World) -> None:
        """A caller must be able to read the envelope and know what it actually got."""
        page = await world.service.list_politicians(limit=10_000, offset=-3)

        assert (page.limit, page.offset) == (MAX_PAGE_LIMIT, 0)


# ---------------------------------------------------------------------------------------
# get_politician
# ---------------------------------------------------------------------------------------


class TestGetPolitician:
    async def test_a_known_roster_id_resolves(self, world: World) -> None:
        result = await world.service.get_politician(politician_id="TX-SEN-R")

        assert result.politician_id == "TX-SEN-R"
        assert result.last_name == "Ashgrove"

    async def test_an_unknown_roster_id_is_a_plain_not_found(self, world: World) -> None:
        """No owner, no 404-instead-of-403 subtlety: the row is simply not there."""
        with pytest.raises(NotFoundError) as caught:
            await world.service.get_politician(politician_id="ZZ-000")

        assert caught.value.details["resource"] == "politician"
        assert caught.value.details["identifier"] == "ZZ-000"

    async def test_surrounding_whitespace_is_trimmed(self, world: World) -> None:
        result = await world.service.get_politician(politician_id="  TX-SEN-R  ")

        assert result.politician_id == "TX-SEN-R"
        assert world.politicians.calls[-1] == ("get_by_id", "TX-SEN-R")

    async def test_the_casing_of_a_roster_id_is_left_alone(self, world: World) -> None:
        """Deliberate: a roster id is opaque, so folding it would hide a real distinction."""
        with pytest.raises(NotFoundError):
            await world.service.get_politician(politician_id="tx-sen-r")

        assert world.politicians.calls[-1] == ("get_by_id", "tx-sen-r")

    async def test_the_error_reports_the_identifier_it_actually_looked_up(
        self, world: World
    ) -> None:
        with pytest.raises(NotFoundError) as caught:
            await world.service.get_politician(politician_id="  ZZ-000 ")

        assert caught.value.details["identifier"] == "ZZ-000"


# ---------------------------------------------------------------------------------------
# seed_roster
# ---------------------------------------------------------------------------------------


class TestSeedRoster:
    @pytest.fixture
    def empty(self, settings: Settings) -> World:
        session = StubSession()
        repo = FakePoliticianRepo()
        return World(
            service=PoliticianService(session, settings, politicians=repo),  # type: ignore[arg-type]
            politicians=repo,
            session=session,
        )

    async def test_it_writes_the_batch_it_was_given(self, empty: World) -> None:
        report = await empty.service.seed_roster(rows=[seed_row("A1"), seed_row("B2")])

        assert report == SeedReport(loaded=2, written=2, duplicates=())
        assert len(empty.politicians.politicians) == 2

    async def test_it_commits(self, empty: World) -> None:
        """A seed that does not commit has done nothing, and the repo only flushes."""
        await empty.service.seed_roster(rows=[seed_row("A1")])

        assert empty.session.commits == 1

    async def test_running_it_twice_leaves_the_same_rows(self, empty: World) -> None:
        """Idempotency, from the service's side: the second run refreshes, never appends."""
        rows = [seed_row("A1"), seed_row("B2"), seed_row("C3")]

        first = await empty.service.seed_roster(rows=rows)
        second = await empty.service.seed_roster(rows=rows)

        assert len(empty.politicians.politicians) == 3
        assert first.written == second.written == 3

    async def test_a_second_run_refreshes_the_values(self, empty: World) -> None:
        await empty.service.seed_roster(rows=[seed_row("A1", party="Democrat")])
        await empty.service.seed_roster(rows=[seed_row("A1", party="Republican")])

        assert [row.party for row in empty.politicians.politicians] == ["Republican"]

    async def test_an_internal_duplicate_is_collapsed_rather_than_crashing(
        self, empty: World
    ) -> None:
        """Postgres refuses a statement whose conflict target is hit twice; the fake does too."""
        report = await empty.service.seed_roster(
            rows=[seed_row("A1"), seed_row("B2"), seed_row("A1")]
        )

        assert report.loaded == 3
        assert report.written == 2
        assert report.duplicates == ("A1",)
        assert report.deduplicated == 1
        assert len(empty.politicians.politicians) == 2

    async def test_the_batch_handed_to_the_repo_never_repeats_an_id(self, empty: World) -> None:
        """The precondition the repo documents and does not enforce."""
        await empty.service.seed_roster(rows=[seed_row("A1"), seed_row("A1"), seed_row("A1")])

        _, batch = empty.politicians.calls[-1]
        identifiers = [row["politician_id"] for row in batch]
        assert identifiers == ["A1"]

    async def test_the_last_occurrence_of_a_duplicate_wins(self, empty: World) -> None:
        await empty.service.seed_roster(
            rows=[seed_row("A1", party="Democrat"), seed_row("A1", party="Libertarian")]
        )

        assert empty.politicians.politicians[0].party == "Libertarian"

    async def test_rows_reach_the_repo_as_plain_column_values(self, empty: World) -> None:
        """``bulk_upsert`` takes mappings for a Core statement, not pydantic models."""
        await empty.service.seed_roster(rows=[seed_row("A1")])

        _, batch = empty.politicians.calls[-1]
        assert isinstance(batch[0], dict)
        assert batch[0]["dob"] == dt.date(1960, 5, 4)
        assert set(batch[0]) == {
            "politician_id",
            "first_name",
            "last_name",
            "party",
            "state",
            "chamber",
            "dob",
            "gender",
        }

    async def test_an_empty_batch_is_a_no_op_not_an_error(self, empty: World) -> None:
        report = await empty.service.seed_roster(rows=[])

        assert report == SeedReport(loaded=0, written=0, duplicates=())
        assert empty.politicians.politicians == []

    async def test_with_no_rows_given_it_loads_the_checked_in_roster(self, empty: World) -> None:
        """The production path: no argument means ``app/data/politicians.json``."""
        report = await empty.service.seed_roster()

        assert report.loaded > 20
        assert report.written == report.loaded
        assert report.duplicates == ()

    async def test_the_checked_in_roster_is_idempotent_too(self, empty: World) -> None:
        first = await empty.service.seed_roster()
        before = len(empty.politicians.politicians)
        second = await empty.service.seed_roster()

        assert len(empty.politicians.politicians) == before
        assert first.written == second.written

    async def test_a_repo_failure_is_not_swallowed(self, empty: World) -> None:
        """A seed that could not write must not report success."""
        empty.politicians.bulk_upsert_error = RuntimeError("connection reset")

        with pytest.raises(RuntimeError, match="connection reset"):
            await empty.service.seed_roster(rows=[seed_row("A1")])

        assert empty.session.commits == 0


# ---------------------------------------------------------------------------------------
# the sweep — one property, every use case, case list derived from the service
# ---------------------------------------------------------------------------------------


class TestEveryUseCase:
    """The transaction property, held across the service's whole public surface.

    ANV-15's pattern: a property that must be true of *every* use case is one parameterised
    sweep whose case list is derived from ``vars(PoliticianService)`` and asserted complete,
    rather than N hand-written tests one of which will be forgotten. The property here is the
    transaction boundary — reference data is read far more often than it is written, and a
    read that commits is a read that can flush somebody else's half-finished unit of work.

    There is deliberately **no ownership sweep**: a legislator has no owner, so there is no
    cross-account case to leak (see ``app/services/politician.py``'s docstring).
    """

    #: Every read, and how to call it. ``seed_roster`` is the exempt writer.
    READS: tuple[tuple[str, dict[str, Any]], ...] = (
        ("list_politicians", {}),
        ("get_politician", {"politician_id": "TX-SEN-R"}),
    )

    EXEMPT = frozenset({"seed_roster"})

    @pytest.mark.parametrize(("use_case", "arguments"), READS, ids=[name for name, _ in READS])
    async def test_a_read_never_commits(
        self, world: World, use_case: str, arguments: dict[str, Any]
    ) -> None:
        await getattr(world.service, use_case)(**arguments)

        assert world.session.commits == 0
        assert world.session.rollbacks == 0

    @pytest.mark.parametrize(("use_case", "arguments"), READS, ids=[name for name, _ in READS])
    async def test_a_read_never_writes(
        self, world: World, use_case: str, arguments: dict[str, Any]
    ) -> None:
        before = list(world.politicians.politicians)

        await getattr(world.service, use_case)(**arguments)

        assert world.politicians.politicians == before
        assert not any(method == "bulk_upsert" for method, _ in world.politicians.calls)

    async def test_the_one_writer_does_commit(self, world: World) -> None:
        await world.service.seed_roster(rows=[seed_row("A1")])

        assert world.session.commits == 1

    def test_the_sweep_covers_every_use_case(self) -> None:
        """The sweep is only as good as its list, so the list is derived from the service.

        ``seed_roster`` is exempt and *named* rather than silently missing — it is the one
        method that is supposed to write, and it has its own class above.
        """
        public = {
            name
            for name in vars(PoliticianService)
            if not name.startswith("_") and callable(getattr(PoliticianService, name))
        }

        assert public - self.EXEMPT == {name for name, _ in self.READS}
