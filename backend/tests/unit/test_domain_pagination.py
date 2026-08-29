"""Unit tests for ``app/domain/pagination.py`` — the paging rule's new, neutral home.

The rule was ANV-14's and lived in ``app/domain/stock_data.py``. ANV-15's ``list_mine`` was
its second caller and ANV-16's ``list_politicians`` is its third, so ``CLAUDE.md`` §4 — "a
pure rule with a second caller moves *down*, never sideways" — moved it here: a rule three
aggregates share belongs to none of them. ANV-14's import path is kept as a re-export.

``tests/unit/test_domain_stock_data.py`` still exercises the behaviour through that
re-export, which is exactly the point of keeping it. **This module covers what the move
itself has to be true for**: that the re-exported names are the *same objects* rather than
copies (a copy would drift, and every existing test would keep passing while it did), and
that the new home is as pure as the old one.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.domain import pagination as domain_pagination
from app.domain import stock_data as domain_stock_data
from app.domain.pagination import MIN_OFFSET, PageWindow, resolve_window
from app.schemas.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT


class TestTheMoveIsAMoveNotACopy:
    """A re-export that is a copy passes every behavioural test and still drifts."""

    def test_resolve_window_is_the_same_function(self) -> None:
        assert domain_stock_data.resolve_window is resolve_window

    def test_page_window_is_the_same_class(self) -> None:
        assert domain_stock_data.PageWindow is PageWindow

    def test_min_offset_is_the_same_value(self) -> None:
        assert domain_stock_data.MIN_OFFSET is MIN_OFFSET

    @pytest.mark.parametrize("name", ["MIN_OFFSET", "PageWindow", "resolve_window"])
    def test_the_old_module_still_advertises_them(self, name: str) -> None:
        """``__all__`` is what keeps a re-export honest rather than incidental."""
        assert name in domain_stock_data.__all__

    def test_the_candle_query_holds_the_shared_window_type(self) -> None:
        """``resolve_candle_query`` still composes the moved rule rather than a local one."""
        query = domain_stock_data.resolve_candle_query(limit=5, offset=2)

        assert query.window == PageWindow(limit=5, offset=2)
        assert type(query.window) is PageWindow

    def test_the_service_that_prompted_the_move_uses_the_same_object(self) -> None:
        from app.services import politician as politician_service
        from app.services import watchlist as watchlist_service

        assert watchlist_service.resolve_window is resolve_window
        assert politician_service.resolve_window is resolve_window


class TestPurity:
    """The new home is under ``app/domain/``, so §3's purity rule applies to it too."""

    @pytest.fixture
    def source(self) -> str:
        return Path(domain_pagination.__file__).read_text(encoding="utf-8")

    def test_it_imports_only_the_schema_that_owns_the_bounds(self, source: str) -> None:
        modules: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)

        assert modules == {"__future__", "dataclasses", "typing", "app.schemas.pagination"}

    @pytest.mark.parametrize(
        "forbidden", ["select(", "session", "get_settings", "httpx", ".now(", "fastapi"]
    )
    def test_it_performs_no_io_and_reads_no_clock(self, source: str, forbidden: str) -> None:
        assert forbidden not in source


class TestResolveWindow:
    """A short restatement of the rule, so this module fails on its own if the move broke it.

    The exhaustive coverage stays in ``tests/unit/test_domain_stock_data.py``, which reaches
    the same function through the re-export.
    """

    def test_nothing_given_is_the_default_page(self) -> None:
        assert resolve_window() == PageWindow(limit=DEFAULT_PAGE_LIMIT, offset=MIN_OFFSET)

    def test_an_over_large_limit_is_clamped_to_the_ceiling(self) -> None:
        assert resolve_window(limit=10_000).limit == MAX_PAGE_LIMIT

    def test_a_negative_offset_is_floored_rather_than_refused(self) -> None:
        assert resolve_window(offset=-7).offset == MIN_OFFSET

    def test_a_window_is_frozen(self) -> None:
        window = resolve_window()

        with pytest.raises(AttributeError):
            window.limit = 1  # type: ignore[misc]
