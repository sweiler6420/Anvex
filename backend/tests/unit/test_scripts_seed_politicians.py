"""Unit tests for ``backend/scripts/seed_politicians.py`` — the entry point, not the seed.

The script is deliberately thin (``CLAUDE.md`` §3: a script has the same shape as a handler
or a task — resolve, call **one** service method, report), so what is worth testing here is
exactly the part that is *not* the service: the exit codes and the failure messages. The
behaviour behind it is covered at unit speed in ``tests/unit/test_services_politician.py``
and against real Postgres in ``tests/integration/test_services_politician.py``.

The exit codes matter because this runs in a deployment step. A seed that could not read its
file and a seed the database refused are different faults with different fixes, and a script
that returned ``0`` for either would let a half-empty roster ship silently.

No database and no fixtures: :func:`scripts.seed_politicians.seed` is replaced, which is the
one seam the entry point has.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from app.data.loader import SeedDataError
from app.services.politician import SeedReport
from scripts import seed_politicians


@pytest.fixture
def report() -> SeedReport:
    return SeedReport(loaded=54, written=54, duplicates=())


def patch_seed(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> None:
    """Point the entry point at ``outcome``: a report to return, or an exception to raise."""

    async def _seed() -> SeedReport:
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(seed_politicians, "seed", _seed)


class TestExitCodes:
    def test_a_successful_seed_exits_zero(
        self, monkeypatch: pytest.MonkeyPatch, report: SeedReport
    ) -> None:
        patch_seed(monkeypatch, report)

        assert seed_politicians.main() == seed_politicians.EXIT_OK

    def test_a_broken_roster_file_exits_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        patch_seed(
            monkeypatch, SeedDataError("row 3 is not a valid PoliticianCreate", path=Path("x"))
        )

        assert seed_politicians.main() == seed_politicians.EXIT_BAD_DATA

    def test_a_database_failure_exits_two(self, monkeypatch: pytest.MonkeyPatch) -> None:
        patch_seed(monkeypatch, OperationalError("SELECT 1", {}, Exception("refused")))

        assert seed_politicians.main() == seed_politicians.EXIT_DATABASE

    def test_the_three_codes_are_distinct(self) -> None:
        """Different faults need different fixes, so they must be distinguishable."""
        codes = {
            seed_politicians.EXIT_OK,
            seed_politicians.EXIT_BAD_DATA,
            seed_politicians.EXIT_DATABASE,
        }

        assert len(codes) == 3

    def test_an_unexpected_error_is_not_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Only the two known faults are turned into exit codes; a bug stays a traceback."""
        patch_seed(monkeypatch, RuntimeError("something else entirely"))

        with pytest.raises(RuntimeError, match="something else entirely"):
            seed_politicians.main()


class TestWhatItPrints:
    def test_it_reports_the_counts(
        self,
        monkeypatch: pytest.MonkeyPatch,
        report: SeedReport,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        patch_seed(monkeypatch, report)

        seed_politicians.main()

        assert "54 row(s) in the file" in capsys.readouterr().out

    def test_it_names_the_duplicates_it_collapsed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A repeated roster id is probably a mistake in the file, so somebody is told."""
        patch_seed(monkeypatch, SeedReport(loaded=3, written=2, duplicates=("A000001",)))

        seed_politicians.main()

        out = capsys.readouterr().out
        assert "1 duplicate(s) collapsed" in out
        assert "A000001" in out

    def test_a_clean_run_says_nothing_about_duplicates(
        self,
        monkeypatch: pytest.MonkeyPatch,
        report: SeedReport,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        patch_seed(monkeypatch, report)

        seed_politicians.main()

        assert "duplicate roster ids" not in capsys.readouterr().out

    def test_a_failure_goes_to_stderr_and_names_the_file(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        patch_seed(monkeypatch, SeedDataError("row 3 is bad", path=Path("politicians.json")))

        seed_politicians.main()

        captured = capsys.readouterr()
        assert captured.out == ""
        assert "politicians.json" in captured.err
        assert "row 3" in captured.err


def test_the_script_calls_one_service_method_and_no_repo() -> None:
    """§3: a script is a thin entry point. Every rule lives behind ``seed_roster``."""
    source = Path(seed_politicians.__file__).read_text(encoding="utf-8")

    assert "seed_roster()" in source
    for forbidden in ("select(", "PoliticianRepo", "bulk_upsert", "dedupe_politicians"):
        assert forbidden not in source, forbidden
