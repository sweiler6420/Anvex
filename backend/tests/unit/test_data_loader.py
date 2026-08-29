"""Unit tests for ``app/data/`` — the loader pattern, and the checked-in roster itself.

ANV-16 is the first user of this layer, so this module pins the pattern as much as the file:
a reference file is an envelope with a required ``provenance`` string and a ``rows`` list,
every row is validated against the resource's ``XCreate`` schema, and **everything that can
be wrong with the file is wrong at load time** rather than as an ``IntegrityError`` partway
through a bulk insert.

Two of these are about the seed data rather than the code, and they are the ones worth
keeping. ``TestTheCheckedInRoster`` parses the real ``politicians.json`` — so a hand-edit
that breaks it fails the suite rather than the next `seed` run — and asserts it is free of
the duplicate roster ids that would make the seed's dedupe step load-bearing by accident.
``TestProvenanceIsHonest`` asserts the file says, in its own text, that it is synthetic:
fabricated reference data presented as sourced data is the failure mode this key exists to
prevent, and an assertion is the only part of that which cannot be forgotten.

No fixtures, no I/O beyond ``tmp_path`` and the repository's own checked-in file — which is
not I/O the ``db`` marker cares about, so this module runs with Docker stopped.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from app.data.loader import (
    DATA_DIR,
    PROVENANCE_KEY,
    ROWS_KEY,
    SeedDataError,
    load_document,
    load_rows,
    provenance_of,
)
from app.data.politicians import (
    POLITICIANS_FILE,
    load_politicians,
    politicians_provenance,
)
from app.domain.errors import AnvexError
from app.schemas.politician import PoliticianCreate

VALID_ROW = {
    "politician_id": "A000001",
    "first_name": "Adelaide",
    "last_name": "Ashgrove",
    "party": "Democrat",
    "state": "CA",
    "chamber": "Senate",
    "dob": "1960-05-04",
    "gender": "F",
}


def write(path: Path, document: Any) -> Path:
    """Write ``document`` as the JSON of a reference file and return the path."""
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.fixture
def roster(tmp_path: Path) -> Path:
    return write(
        tmp_path / "roster.json",
        {PROVENANCE_KEY: "Synthetic, written by this test.", ROWS_KEY: [VALID_ROW]},
    )


# ---------------------------------------------------------------------------------------
# the envelope
# ---------------------------------------------------------------------------------------


class TestTheEnvelope:
    def test_a_well_formed_file_yields_its_provenance_and_rows(self, roster: Path) -> None:
        provenance, rows = load_document(roster)

        assert provenance == "Synthetic, written by this test."
        assert rows == [VALID_ROW]

    def test_provenance_of_returns_just_the_attribution(self, roster: Path) -> None:
        assert provenance_of(roster) == "Synthetic, written by this test."

    def test_a_missing_file_names_itself(self, tmp_path: Path) -> None:
        missing = tmp_path / "absent.json"

        with pytest.raises(SeedDataError) as caught:
            load_document(missing)

        assert caught.value.path == missing
        assert str(missing) in str(caught.value)

    def test_a_file_that_is_not_json_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "roster.json"
        path.write_text("{not json at all", encoding="utf-8")

        with pytest.raises(SeedDataError, match="not valid JSON"):
            load_document(path)

    def test_a_bare_array_is_refused(self, tmp_path: Path) -> None:
        """The envelope is the pattern: a bare array has nowhere to put its attribution."""
        path = write(tmp_path / "roster.json", [VALID_ROW])

        with pytest.raises(SeedDataError, match="must be a JSON object"):
            load_document(path)

    @pytest.mark.parametrize(
        "provenance",
        [None, "", "   ", 42, ["a list"]],
        ids=["missing", "empty", "whitespace", "a number", "a list"],
    )
    def test_a_file_without_real_attribution_cannot_be_loaded(
        self, tmp_path: Path, provenance: Any
    ) -> None:
        """The whole point of the key: unattributed reference data does not load at all."""
        document: dict[str, Any] = {ROWS_KEY: [VALID_ROW]}
        if provenance is not None:
            document[PROVENANCE_KEY] = provenance
        path = write(tmp_path / "roster.json", document)

        with pytest.raises(SeedDataError, match=PROVENANCE_KEY):
            load_document(path)

    @pytest.mark.parametrize(
        "rows", [None, {}, "rows", 7], ids=["missing", "an object", "a string", "a number"]
    )
    def test_rows_must_be_a_list(self, tmp_path: Path, rows: Any) -> None:
        document: dict[str, Any] = {PROVENANCE_KEY: "Synthetic."}
        if rows is not None:
            document[ROWS_KEY] = rows
        path = write(tmp_path / "roster.json", document)

        with pytest.raises(SeedDataError, match=ROWS_KEY):
            load_document(path)

    def test_an_empty_roster_is_legal(self, tmp_path: Path) -> None:
        """A file with nothing in it is a file with nothing in it, not a broken file."""
        path = write(tmp_path / "roster.json", {PROVENANCE_KEY: "Synthetic.", ROWS_KEY: []})

        assert load_rows(path, PoliticianCreate) == []

    def test_keys_beyond_the_two_required_ones_are_ignored(self, tmp_path: Path) -> None:
        """So a file may carry a ``generated`` date or a version without a schema change."""
        path = write(
            tmp_path / "roster.json",
            {
                PROVENANCE_KEY: "Synthetic.",
                "generated": "2026-08-29",
                "version": 3,
                ROWS_KEY: [VALID_ROW],
            },
        )

        assert len(load_rows(path, PoliticianCreate)) == 1


# ---------------------------------------------------------------------------------------
# the rows
# ---------------------------------------------------------------------------------------


class TestRowValidation:
    def test_rows_come_back_as_the_create_schema_not_dicts(self, roster: Path) -> None:
        """Which is what makes the seed path and an HTTP body agree on what a valid row is."""
        rows = load_rows(roster, PoliticianCreate)

        assert [type(row) for row in rows] == [PoliticianCreate]
        assert rows[0].politician_id == "A000001"

    def test_a_date_string_is_parsed_into_a_date(self, roster: Path) -> None:
        """``dob`` reaches the repo as a ``date``, which is what the ``DATE`` column takes."""
        row = load_rows(roster, PoliticianCreate)[0]

        assert (row.dob.year, row.dob.month, row.dob.day) == (1960, 5, 4)  # type: ignore[union-attr]

    def test_the_four_nullable_columns_may_be_null(self, tmp_path: Path) -> None:
        sparse = {
            "politician_id": "B000002",
            "first_name": "Bartholomew",
            "last_name": "Blackwater",
            "party": "Independent",
            "state": None,
            "chamber": None,
            "dob": None,
            "gender": None,
        }
        path = write(tmp_path / "roster.json", {PROVENANCE_KEY: "Synthetic.", ROWS_KEY: [sparse]})

        row = load_rows(path, PoliticianCreate)[0]

        assert (row.state, row.chamber, row.dob, row.gender) == (None, None, None, None)

    @pytest.mark.parametrize(
        ("mutation", "reason"),
        [
            ({"party": None}, "a required column is null"),
            ({"first_name": ""}, "a name is empty"),
            ({"last_name": "x" * 81}, "a name is longer than the column"),
            ({"dob": "March the fourth"}, "a date is not a date"),
            ({"politician_id": None}, "the primary key is null"),
            ({"state": "CALIFORNIA"}, "a state exceeds the five-character column"),
        ],
    )
    def test_a_malformed_row_fails_at_load_naming_its_index(
        self, tmp_path: Path, mutation: dict[str, Any], reason: str
    ) -> None:
        """The index is what somebody fixing a fifty-four-row file actually needs."""
        rows = [VALID_ROW, VALID_ROW, {**VALID_ROW, **mutation}]
        path = write(tmp_path / "roster.json", {PROVENANCE_KEY: "Synthetic.", ROWS_KEY: rows})

        with pytest.raises(SeedDataError, match="row 2") as caught:
            load_rows(path, PoliticianCreate)

        assert "PoliticianCreate" in str(caught.value), reason

    def test_a_row_that_is_not_an_object_is_refused(self, tmp_path: Path) -> None:
        path = write(
            tmp_path / "roster.json",
            {PROVENANCE_KEY: "Synthetic.", ROWS_KEY: [VALID_ROW, "A000002"]},
        )

        with pytest.raises(SeedDataError, match="row 1 is a str"):
            load_rows(path, PoliticianCreate)

    def test_a_seed_data_error_is_a_value_error(self) -> None:
        """``app/data/`` has no Anvex error vocabulary — a broken file is a defect, not a 4xx.

        See ``app/data/loader.py``'s docstring: the seed path is reached from a script, never
        from a route, so there is no status code for this to become and it must not inherit
        one by accident.
        """
        assert issubclass(SeedDataError, ValueError)
        assert not issubclass(SeedDataError, AnvexError)


# ---------------------------------------------------------------------------------------
# the layer's own rules
# ---------------------------------------------------------------------------------------


class TestTheLayerStaysInItsLane:
    """``CLAUDE.md`` §3: a loader parses and returns. No network, no database.

    Asserted by reading the source rather than trusting the docstring — a purity convention
    that lives only in prose gets broken, which is the argument ``tests/unit/
    test_domain_auth.py`` makes for the domain layer and it applies just as well here.
    """

    @pytest.fixture
    def sources(self) -> dict[str, str]:
        return {
            path.name: path.read_text(encoding="utf-8")
            for path in DATA_DIR.glob("*.py")
            if path.name != "__init__.py"
        }

    def test_there_is_at_least_one_module_to_check(self, sources: dict[str, str]) -> None:
        assert set(sources) >= {"loader.py", "politicians.py"}

    @pytest.mark.parametrize(
        "forbidden",
        ["sqlalchemy", "select(", "AsyncSession", "httpx", "requests", "app.repos", "app.db"],
    )
    def test_no_loader_reaches_for_a_database_or_the_network(
        self, sources: dict[str, str], forbidden: str
    ) -> None:
        for name, source in sources.items():
            assert forbidden not in source, f"{name} mentions {forbidden}"

    def test_a_loader_may_import_schemas_and_nothing_else_from_app(
        self, sources: dict[str, str]
    ) -> None:
        """Validating a row needs the ``XCreate`` contract; nothing else is in reach."""
        imported: set[str] = set()
        for source in sources.values():
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
                elif isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)

        assert {module for module in imported if module.startswith("app.")} == {
            "app.data.loader",
            "app.schemas.politician",
        }


# ---------------------------------------------------------------------------------------
# the file that actually ships
# ---------------------------------------------------------------------------------------


class TestTheCheckedInRoster:
    def test_the_file_is_where_the_loader_looks(self) -> None:
        assert POLITICIANS_FILE == DATA_DIR / "politicians.json"
        assert POLITICIANS_FILE.is_file()

    def test_it_parses_and_every_row_is_valid(self) -> None:
        """A hand-edit that breaks the roster fails the suite, not the next seed run."""
        rows = load_politicians()

        assert len(rows) > 20
        assert all(isinstance(row, PoliticianCreate) for row in rows)

    def test_every_roster_id_is_unique(self) -> None:
        """Not required — the seed deduplicates — but a duplicate here would be a mistake."""
        identifiers = [row.politician_id for row in load_politicians()]

        assert len(set(identifiers)) == len(identifiers)

    def test_it_exercises_all_four_nullable_columns(self) -> None:
        """A fixture where nothing is null never proves the nullable columns are nullable."""
        rows = load_politicians()

        for column in ("state", "chamber", "dob", "gender"):
            assert any(getattr(row, column) is None for row in rows), column

    def test_it_holds_more_than_one_state_party_and_chamber(self) -> None:
        """Otherwise the filter tests downstream would be filtering on a constant."""
        rows = load_politicians()

        assert len({row.state for row in rows if row.state}) > 1
        assert len({row.party for row in rows}) > 1
        assert len({row.chamber for row in rows if row.chamber}) > 1


class TestProvenanceIsHonest:
    def test_the_roster_declares_itself_synthetic(self) -> None:
        """Fabricated reference data presented as sourced data is the failure this prevents.

        The data in ``politicians.json`` was generated, not collected, and the file has to
        keep saying so — an assertion is the only part of that which cannot be forgotten
        when somebody appends a row.
        """
        provenance = politicians_provenance().casefold()

        assert "synthetic" in provenance
        assert "invented" in provenance

    def test_it_does_not_claim_a_source_it_does_not_have(self) -> None:
        provenance = politicians_provenance().casefold()

        assert "not copied" in provenance or "not real" in provenance
