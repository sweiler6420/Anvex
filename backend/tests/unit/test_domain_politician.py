"""Unit tests for ``app/domain/politician.py`` — filter normalisation and seed dedupe.

Two rules, tested exhaustively because they are pure and therefore cheap to test that way
(``CLAUDE.md`` §3).

**Normalisation is the rule that makes the repo's exactness usable.**
``PoliticianRepo``'s filters are ``==`` comparisons, so ``state="tx"`` matches nothing at
all; every casing test here is a case that would otherwise be an empty page a user cannot
explain. The interesting half is the *non*-rewriting: an unrecognised party is passed through
trimmed rather than corrected or refused, because Anvex does not own that vocabulary.

**Dedupe is the rule that stops the seed crashing on itself.** Postgres refuses an
``ON CONFLICT DO UPDATE`` whose target is hit twice, and the repo deliberately does not
deduplicate, so a batch containing a repeated roster id must be collapsed before it becomes a
statement. The property sweep at the bottom holds the three things that must be true of every
batch, on batches generated rather than hand-picked.
"""

from __future__ import annotations

import ast
import datetime as dt
import itertools
from pathlib import Path

import pytest

from app.domain import politician as domain_politician
from app.domain.politician import (
    CHAMBERS,
    PARTIES,
    RESOURCE,
    RosterBatch,
    dedupe_politicians,
    normalise_chamber,
    normalise_party,
    normalise_state,
    resolve_filters,
)
from app.schemas.politician import PoliticianCreate


def row(politician_id: str, **overrides: object) -> PoliticianCreate:
    """One valid roster row, varying only what a test cares about."""
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
# purity
# ---------------------------------------------------------------------------------------


class TestPurity:
    """``app/domain/`` is pure by rule, and prose conventions get broken (§3)."""

    @pytest.fixture
    def source(self) -> str:
        return Path(domain_politician.__file__).read_text(encoding="utf-8")

    def test_it_imports_only_the_schema_it_speaks_in(self, source: str) -> None:
        modules: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)

        assert modules == {
            "__future__",
            "collections.abc",
            "dataclasses",
            "typing",
            "app.schemas.politician",
        }

    @pytest.mark.parametrize(
        "forbidden",
        ["select(", "session", "get_settings", "httpx", ".now(", "utcnow", "fastapi"],
    )
    def test_it_performs_no_io_and_reads_no_clock(self, source: str, forbidden: str) -> None:
        assert forbidden not in source


# ---------------------------------------------------------------------------------------
# normalisation
# ---------------------------------------------------------------------------------------


class TestNormaliseState:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [("TX", "TX"), ("tx", "TX"), ("Tx", "TX"), ("  tx  ", "TX"), ("\tny\n", "NY")],
    )
    def test_case_and_surrounding_whitespace_are_the_only_things_touched(
        self, given: str, expected: str
    ) -> None:
        assert normalise_state(given) == expected

    @pytest.mark.parametrize("given", [None, "", "   ", "\t\n"])
    def test_an_absent_or_blank_filter_is_no_filter(self, given: str | None) -> None:
        """``?state=`` must mean "everybody", not "nobody named the empty string"."""
        assert normalise_state(given) is None

    def test_it_is_idempotent(self) -> None:
        assert normalise_state(normalise_state(" tx ")) == "TX"


class TestNormaliseParty:
    @pytest.mark.parametrize("canonical", PARTIES)
    @pytest.mark.parametrize("transform", [str.lower, str.upper, str.title, lambda text: text])
    def test_every_known_party_survives_every_casing(
        self, canonical: str, transform: object
    ) -> None:
        assert normalise_party(transform(canonical)) == canonical  # type: ignore[operator]

    def test_surrounding_whitespace_goes_too(self) -> None:
        assert normalise_party("  republican ") == "Republican"

    @pytest.mark.parametrize("given", [None, "", "   "])
    def test_an_absent_or_blank_filter_is_no_filter(self, given: str | None) -> None:
        assert normalise_party(given) is None

    def test_an_unknown_party_is_passed_through_trimmed_not_rewritten(self) -> None:
        """The column is free text; a party Anvex has not heard of is a legitimate query."""
        assert normalise_party("  Whig  ") == "Whig"

    def test_an_unknown_party_is_not_refused(self) -> None:
        """It becomes an empty page in the service, never a 422 — see the module docstring."""
        assert normalise_party("Whig") == "Whig"

    def test_an_unknown_party_keeps_its_own_casing(self) -> None:
        """Because the alternative is answering a question the caller did not ask."""
        assert normalise_party("wHiG") == "wHiG"


class TestNormaliseChamber:
    @pytest.mark.parametrize("canonical", CHAMBERS)
    @pytest.mark.parametrize("transform", [str.lower, str.upper, str.title])
    def test_every_known_chamber_survives_every_casing(
        self, canonical: str, transform: object
    ) -> None:
        assert normalise_chamber(transform(canonical)) == canonical  # type: ignore[operator]

    @pytest.mark.parametrize("given", [None, "", "  "])
    def test_an_absent_or_blank_filter_is_no_filter(self, given: str | None) -> None:
        assert normalise_chamber(given) is None

    def test_an_unknown_chamber_is_passed_through(self) -> None:
        assert normalise_chamber(" Politburo ") == "Politburo"


class TestResolveFilters:
    def test_it_applies_all_three_rules_at_once(self) -> None:
        filters = resolve_filters(state=" tx ", party="REPUBLICAN", chamber="senate")

        assert (filters.state, filters.party, filters.chamber) == ("TX", "Republican", "Senate")

    def test_nothing_given_is_the_whole_roster(self) -> None:
        filters = resolve_filters()

        assert filters.is_unfiltered
        assert (filters.state, filters.party, filters.chamber) == (None, None, None)

    def test_blank_strings_are_the_whole_roster_too(self) -> None:
        assert resolve_filters(state="", party="  ", chamber="\t").is_unfiltered

    def test_one_filter_given_is_not_unfiltered(self) -> None:
        assert not resolve_filters(state="tx").is_unfiltered

    def test_the_result_is_frozen(self) -> None:
        """A service must not be able to widen a filter after resolving it."""
        filters = resolve_filters(state="tx")

        with pytest.raises(AttributeError):
            filters.state = "CA"  # type: ignore[misc]


# ---------------------------------------------------------------------------------------
# dedupe
# ---------------------------------------------------------------------------------------


class TestDedupePoliticians:
    def test_a_clean_batch_is_returned_unchanged(self) -> None:
        rows = [row("A1"), row("B2"), row("C3")]

        batch = dedupe_politicians(rows)

        assert [entry.politician_id for entry in batch.rows] == ["A1", "B2", "C3"]
        assert batch.duplicates == ()
        assert not batch.has_duplicates

    def test_an_empty_batch_is_empty(self) -> None:
        assert dedupe_politicians([]) == RosterBatch(rows=(), duplicates=())

    def test_a_repeated_id_is_collapsed_to_one_row(self) -> None:
        batch = dedupe_politicians([row("A1"), row("B2"), row("A1")])

        assert [entry.politician_id for entry in batch.rows] == ["A1", "B2"]

    def test_the_last_occurrence_wins(self) -> None:
        """What a sequential loop of upserts would have left behind — see the docstring."""
        batch = dedupe_politicians([row("A1", party="Democrat"), row("A1", party="Republican")])

        assert [entry.party for entry in batch.rows] == ["Republican"]

    def test_ordering_follows_first_appearance(self) -> None:
        """So two runs of the same file build the same statement and their logs diff."""
        batch = dedupe_politicians([row("A1"), row("B2"), row("A1"), row("C3")])

        assert [entry.politician_id for entry in batch.rows] == ["A1", "B2", "C3"]

    def test_the_duplicated_ids_are_reported(self) -> None:
        batch = dedupe_politicians([row("A1"), row("B2"), row("A1"), row("B2"), row("A1")])

        assert batch.duplicates == ("A1", "B2")
        assert batch.has_duplicates

    def test_an_id_repeated_three_times_is_reported_once(self) -> None:
        assert dedupe_politicians([row("A1")] * 3).duplicates == ("A1",)

    def test_matching_is_case_sensitive(self) -> None:
        """The primary key is; folding here would drop a row Postgres would have kept."""
        batch = dedupe_politicians([row("A1"), row("a1")])

        assert [entry.politician_id for entry in batch.rows] == ["A1", "a1"]
        assert batch.duplicates == ()

    def test_it_accepts_any_iterable_not_just_a_list(self) -> None:
        """The service hands it whatever the loader produced; a generator is fair game."""
        batch = dedupe_politicians(entry for entry in [row("A1"), row("A1")])

        assert len(batch.rows) == 1

    def test_the_batch_is_frozen(self) -> None:
        batch = dedupe_politicians([row("A1")])

        with pytest.raises(AttributeError):
            batch.rows = ()  # type: ignore[misc]


class TestTheDedupeProperties:
    """The three properties that must hold for **every** batch, over generated batches.

    The sweep is over every arrangement of up to four rows drawn from three ids, which is
    2 + 4 + 8 + 16 + … arrangements rather than the handful a person would think to write —
    and it is the shape ANV-15 used for the reorder rule, applied to the other rule whose
    correctness the seed depends on.
    """

    @pytest.fixture(params=[1, 2, 3, 4])
    def batches(self, request: pytest.FixtureRequest) -> list[list[PoliticianCreate]]:
        ids = ["A1", "B2", "C3"]
        return [
            [row(identifier, party=f"P{index}") for index, identifier in enumerate(combination)]
            for combination in itertools.product(ids, repeat=request.param)
        ]

    def test_the_result_never_repeats_an_id(self, batches: list[list[PoliticianCreate]]) -> None:
        """The property the whole rule exists for: a statement Postgres will accept."""
        for rows in batches:
            identifiers = [entry.politician_id for entry in dedupe_politicians(rows).rows]

            assert len(set(identifiers)) == len(identifiers), rows

    def test_every_id_in_the_batch_survives(self, batches: list[list[PoliticianCreate]]) -> None:
        """Deduplicating is not filtering: nobody is dropped, only their earlier copies."""
        for rows in batches:
            batch = dedupe_politicians(rows)

            assert {entry.politician_id for entry in batch.rows} == {
                entry.politician_id for entry in rows
            }

    def test_the_surviving_row_is_the_last_one_written(
        self, batches: list[list[PoliticianCreate]]
    ) -> None:
        for rows in batches:
            batch = dedupe_politicians(rows)

            for entry in batch.rows:
                last = [
                    candidate
                    for candidate in rows
                    if candidate.politician_id == entry.politician_id
                ][-1]
                assert entry == last

    def test_it_is_idempotent(self, batches: list[list[PoliticianCreate]]) -> None:
        """A deduplicated batch deduplicates to itself, so a double call is harmless."""
        for rows in batches:
            once = dedupe_politicians(rows)

            assert dedupe_politicians(once.rows).rows == once.rows

    def test_a_deduplicated_batch_reports_no_duplicates(
        self, batches: list[list[PoliticianCreate]]
    ) -> None:
        for rows in batches:
            assert dedupe_politicians(dedupe_politicians(rows).rows).duplicates == ()


def test_the_resource_noun_is_the_one_the_api_reports() -> None:
    """``details["resource"]`` is a client's branch key, so it is spelled in one place."""
    from app.services.politician import RESOURCE as service_resource

    assert RESOURCE == service_resource == "politician"
