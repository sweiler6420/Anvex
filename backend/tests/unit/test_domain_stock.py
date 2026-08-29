"""Unit tests for ``app/domain/stock.py`` — the ticker normalisation rule.

The rule was ANV-13's and lived in ``app/services/stock.py``; ANV-14 gave it a second caller
and moved it down to ``app/domain/`` where a rule belongs (``CLAUDE.md`` §3).
``tests/unit/test_services_stock.py`` still exercises it through the re-export, which is the
point of keeping the re-export — this module covers the rule itself, and the purity the new
home now obliges it to have.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.domain import stock as domain_stock
from app.domain.stock import normalise_ticker
from app.services.stock import normalise_ticker as reexported


def source_tree() -> ast.Module:
    return ast.parse(Path(domain_stock.__file__).read_text(encoding="utf-8"))


class TestPurity:
    """``app/domain/`` is pure by rule, and prose conventions get broken (§3)."""

    def test_the_module_imports_nothing_at_all(self) -> None:
        """It needs no dependency to state the rule, and so it declares none."""
        modules: set[str] = set()
        for node in ast.walk(source_tree()):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)

        assert modules == {"__future__"}

    def test_the_module_performs_no_io(self) -> None:
        source = Path(domain_stock.__file__).read_text(encoding="utf-8")
        for forbidden in ("select(", "session", "get_settings", "httpx", ".now("):
            assert forbidden not in source, forbidden


class TestNormaliseTicker:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("AAPL", "AAPL"),
            ("aapl", "AAPL"),
            ("AaPl", "AAPL"),
            ("  AAPL  ", "AAPL"),
            ("\taapl\n", "AAPL"),
            (" nvda", "NVDA"),
        ],
    )
    def test_case_and_surrounding_whitespace_are_the_only_things_touched(
        self, given: str, expected: str
    ) -> None:
        assert normalise_ticker(given) == expected

    @pytest.mark.parametrize("symbol", ["BRK.B", "BF-B", "RDS.A"])
    def test_real_punctuation_in_a_real_symbol_survives(self, symbol: str) -> None:
        """Dots and hyphens are part of the ticker; stripping them would corrupt the lookup."""
        assert normalise_ticker(symbol.lower()) == symbol

    def test_it_is_idempotent(self) -> None:
        """A canonical spelling normalises to itself, so applying it twice is safe."""
        assert normalise_ticker(normalise_ticker("  aapl ")) == "AAPL"

    def test_an_empty_string_stays_empty_rather_than_raising(self) -> None:
        """Emptiness is a *lookup* failure — a 404 from the service — not a crash here."""
        assert normalise_ticker("   ") == ""

    def test_the_service_re_export_is_the_same_function(self) -> None:
        """ANV-13's import path still resolves, to this exact object rather than a copy."""
        assert reexported is normalise_ticker
