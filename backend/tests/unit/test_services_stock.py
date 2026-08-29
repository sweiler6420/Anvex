"""Unit tests for ``app/services/stock.py`` — the service's own logic, no database.

The fast tier (``CLAUDE.md`` §6): :class:`tests.helpers.FakeStockRepo` stands in for the
repo, so every branch below runs with Docker stopped. What is *not* asserted here is SQL —
that ``ilike`` escapes ``%`` and that the ticker ordering is stable across pages are
database guarantees, proved in ``tests/integration/test_repos_stock.py``.

Four properties are being pinned, and they are different properties:

1. **The service builds the envelope, and builds it honestly.** A repo returns
   ``(rows, total)`` with ``total`` counted *before* the window (``CLAUDE.md`` §3), so an
   ``offset`` past the end must be an empty page with a truthful, non-zero ``total`` —
   never an implied "that is the end of the collection".
2. **The limit is resolved here.** ``limit`` is a required keyword on every paginated repo
   method because a repo cannot import ``app.schemas``; supplying
   ``DEFAULT_PAGE_LIMIT`` and clamping at ``MAX_PAGE_LIMIT`` is therefore the service's.
   The clamp is not cosmetic: ``Page.limit`` carries ``le=MAX_PAGE_LIMIT``, so an unclamped
   value would fail the envelope's own validation and become a 500.
3. **Ticker normalisation is the service's job.**
   :meth:`~app.repos.stock.StockRepo.get_by_ticker` is exact and case-sensitive on purpose,
   and :class:`~tests.helpers.FakeStockRepo` reproduces that faithfully — so a service that
   forgot to upper-case would fail these tests rather than quietly pass them.
4. **A missing row is a** :class:`~app.domain.errors.NotFoundError`, by id and by ticker,
   with the canonical spelling of the identifier in the message.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path as FilePath
from typing import Any

import pytest

from app.domain.errors import AnvexError, NotFoundError
from app.schemas.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page
from app.schemas.stock import StockOut
from app.services import stock as stock_service_module
from app.services.stock import RESOURCE, StockService, normalise_ticker
from app.settings import Settings
from tests.helpers import FakeStockRepo, StubSession, make_stock

APPLE = ("AAPL", "Apple Inc.")
NVIDIA = ("NVDA", "NVIDIA Corporation")
MICROSOFT = ("MSFT", "Microsoft Corporation")


def build_settings(**overrides: Any) -> Settings:
    """Settings that ignore the developer's ``.env``. The stock service reads none of them."""
    values: dict[str, Any] = {"jwt_secret_key": "unit-test-jwt-secret"}
    values.update(overrides)
    return Settings(**values)


def build_service(*stocks: Any) -> tuple[StockService, FakeStockRepo, StubSession]:
    """A :class:`StockService` over an in-memory repo and a counting session stub."""
    repo = FakeStockRepo(*stocks)
    session = StubSession()
    service = StockService(session=session, settings=build_settings(), stocks=repo)  # type: ignore[arg-type]
    return service, repo, session


def catalogue(count: int) -> list[Any]:
    """``count`` stocks with strictly increasing tickers, so paging order is unambiguous."""
    return [
        make_stock(ticker_symbol=f"S{index:04d}", company=f"Security {index} Inc.")
        for index in range(count)
    ]


def last_list_call(repo: FakeStockRepo) -> dict[str, Any]:
    """The keyword arguments the service passed to the repo's paginated method."""
    return next(argument for name, argument in reversed(repo.calls) if name == "list_stocks")


# ---------------------------------------------------------------------------------------
# list_stocks — the envelope
# ---------------------------------------------------------------------------------------


class TestListStocksEnvelope:
    async def test_a_list_comes_back_as_a_page_of_the_output_schema(self) -> None:
        service, _, _ = build_service(*catalogue(3))

        page = await service.list_stocks()

        assert isinstance(page, Page)
        assert [type(item) for item in page.items] == [StockOut] * 3
        assert (page.total, page.limit, page.offset) == (3, DEFAULT_PAGE_LIMIT, 0)

    async def test_no_orm_instance_reaches_the_caller(self) -> None:
        """``CLAUDE.md`` §3: never return an ORM model from a service."""
        service, repo, _ = build_service(*catalogue(2))

        page = await service.list_stocks()

        assert all(item not in repo.stocks for item in page.items)
        assert page.items[0].model_dump().keys() == {
            "stock_id",
            "ticker_symbol",
            "company",
            "market",
            "isin",
        }

    async def test_a_full_window_reports_more_to_come(self) -> None:
        service, _, _ = build_service(*catalogue(10))

        page = await service.list_stocks(limit=4)

        assert len(page.items) == 4
        assert page.total == 10
        assert page.has_more is True

    async def test_the_last_window_reports_no_more(self) -> None:
        service, _, _ = build_service(*catalogue(10))

        page = await service.list_stocks(limit=4, offset=8)

        assert len(page.items) == 2
        assert page.has_more is False

    async def test_an_offset_past_the_end_is_an_empty_page_with_a_truthful_total(
        self,
    ) -> None:
        """The reason ``total`` is counted before the window (``CLAUDE.md`` §3).

        A client that jumps to page 99 of a four-row list must still be told the list has
        four rows, or its paging control has no way back.
        """
        service, _, _ = build_service(*catalogue(4))

        page = await service.list_stocks(limit=10, offset=99)

        assert page.items == []
        assert page.total == 4
        assert page.offset == 99
        assert page.has_more is False

    async def test_an_empty_table_is_an_empty_page_not_an_error(self) -> None:
        service, _, _ = build_service()

        page = await service.list_stocks()

        assert (page.items, page.total, page.has_more) == ([], 0, False)

    async def test_the_window_bounds_are_echoed_back(self) -> None:
        """The response is self-describing: a cached body is interpretable without its request."""
        service, _, _ = build_service(*catalogue(20))

        page = await service.list_stocks(limit=5, offset=10)

        assert (page.limit, page.offset) == (5, 10)

    async def test_listing_reads_nothing_and_commits_nothing(self) -> None:
        """A read has no transaction boundary to own."""
        service, _, session = build_service(*catalogue(3))

        await service.list_stocks()

        assert (session.commits, session.rollbacks) == (0, 0)


# ---------------------------------------------------------------------------------------
# list_stocks — the limit
# ---------------------------------------------------------------------------------------


class TestListStocksLimit:
    async def test_an_unspecified_limit_becomes_the_default(self) -> None:
        """``limit`` is a required keyword on the repo method, so the default is supplied here."""
        service, repo, _ = build_service(*catalogue(3))

        page = await service.list_stocks()

        assert last_list_call(repo)["limit"] == DEFAULT_PAGE_LIMIT
        assert page.limit == DEFAULT_PAGE_LIMIT

    async def test_a_limit_above_the_ceiling_is_clamped_to_it(self) -> None:
        """Not merely tidy: ``Page.limit`` has ``le=MAX_PAGE_LIMIT``, so an unclamped value
        would fail the envelope's own validation and surface as a 500."""
        service, repo, _ = build_service(*catalogue(3))

        page = await service.list_stocks(limit=10_000)

        assert last_list_call(repo)["limit"] == MAX_PAGE_LIMIT
        assert page.limit == MAX_PAGE_LIMIT

    async def test_the_clamped_limit_is_what_the_repo_is_actually_asked_for(self) -> None:
        """The clamp protects Postgres too — the whole table is not fetched and then trimmed."""
        service, repo, _ = build_service(*catalogue(3))

        await service.list_stocks(limit=MAX_PAGE_LIMIT + 1)

        assert last_list_call(repo)["limit"] == MAX_PAGE_LIMIT

    @pytest.mark.parametrize("limit", [0, -1, -100])
    async def test_a_nonsensical_limit_is_clamped_up_to_one(self, limit: int) -> None:
        """``Page.limit`` also has ``ge=1``; ``LIMIT 0`` would be a silently empty page."""
        service, repo, _ = build_service(*catalogue(3))

        page = await service.list_stocks(limit=limit)

        assert last_list_call(repo)["limit"] == 1
        assert page.limit == 1
        assert len(page.items) == 1

    async def test_a_limit_inside_the_bounds_is_passed_through_untouched(self) -> None:
        service, repo, _ = build_service(*catalogue(20))

        page = await service.list_stocks(limit=7)

        assert last_list_call(repo)["limit"] == 7
        assert (page.limit, len(page.items)) == (7, 7)


# ---------------------------------------------------------------------------------------
# list_stocks — search
# ---------------------------------------------------------------------------------------


class TestListStocksSearch:
    def catalogue(self) -> list[Any]:
        return [
            make_stock(ticker_symbol=ticker, company=company)
            for ticker, company in (APPLE, NVIDIA, MICROSOFT)
        ]

    async def test_a_search_narrows_the_page_and_the_total_together(self) -> None:
        service, _, _ = build_service(*self.catalogue())

        page = await service.list_stocks(search="nvda")

        assert [item.ticker_symbol for item in page.items] == ["NVDA"]
        assert page.total == 1

    async def test_the_company_name_is_searchable_too(self) -> None:
        """ "nvidia" is how a person looks for NVDA."""
        service, _, _ = build_service(*self.catalogue())

        page = await service.list_stocks(search="nvidia")

        assert [item.ticker_symbol for item in page.items] == ["NVDA"]

    async def test_the_search_term_reaches_the_repo_unmangled(self) -> None:
        """Case folding and ``%``-escaping are the query's job, not the service's."""
        service, repo, _ = build_service(*self.catalogue())

        await service.list_stocks(search="  Nvid  ")

        assert last_list_call(repo)["search"] == "  Nvid  "

    async def test_no_search_means_no_filter(self) -> None:
        service, _, _ = build_service(*self.catalogue())

        page = await service.list_stocks()

        assert page.total == 3

    async def test_a_search_matching_nothing_is_an_empty_page(self) -> None:
        service, _, _ = build_service(*self.catalogue())

        page = await service.list_stocks(search="zzzz")

        assert (page.items, page.total, page.has_more) == ([], 0, False)


# ---------------------------------------------------------------------------------------
# get_stock
# ---------------------------------------------------------------------------------------


class TestGetStock:
    async def test_an_existing_id_resolves_to_the_output_schema(self) -> None:
        stock = make_stock()
        service, _, _ = build_service(stock)

        found = await service.get_stock(stock_id=stock.stock_id)

        assert isinstance(found, StockOut)
        assert found.stock_id == stock.stock_id
        assert found.ticker_symbol == "AAPL"

    async def test_an_unknown_id_is_a_not_found_error(self) -> None:
        service, _, _ = build_service(make_stock())
        missing = uuid.uuid4()

        with pytest.raises(NotFoundError) as caught:
            await service.get_stock(stock_id=missing)

        assert caught.value.code == "not_found"
        assert caught.value.details == {"resource": RESOURCE, "identifier": str(missing)}

    async def test_a_nullable_isin_survives_the_projection_as_none(self) -> None:
        """``isin`` is the one nullable column on this table (ANV-7)."""
        stock = make_stock(isin=None)
        service, _, _ = build_service(stock)

        found = await service.get_stock(stock_id=stock.stock_id)

        assert found.isin is None


# ---------------------------------------------------------------------------------------
# get_stock_by_ticker — normalisation
# ---------------------------------------------------------------------------------------


class TestGetStockByTicker:
    async def test_the_canonical_ticker_resolves(self) -> None:
        service, _, _ = build_service(make_stock(ticker_symbol="AAPL"))

        found = await service.get_stock_by_ticker(ticker="AAPL")

        assert found.ticker_symbol == "AAPL"

    @pytest.mark.parametrize("given", ["aapl", "AaPl", " aapl ", "\tAAPL\n", "  AaPL  "])
    async def test_any_casing_or_padding_resolves_to_the_same_row(self, given: str) -> None:
        """The repo lookup is exact and case-sensitive by design, and so is the fake — a
        service that skipped the normalisation would raise ``NotFoundError`` here."""
        stock = make_stock(ticker_symbol="AAPL")
        service, _, _ = build_service(stock)

        found = await service.get_stock_by_ticker(ticker=given)

        assert found.stock_id == stock.stock_id

    async def test_the_repo_is_asked_for_the_canonical_spelling(self) -> None:
        """Pinned at the boundary, not just at the result: the unique index only serves an
        exact match, so what the service *sends* is the thing that matters."""
        service, repo, _ = build_service(make_stock(ticker_symbol="AAPL"))

        await service.get_stock_by_ticker(ticker="  aapl  ")

        assert repo.calls == [("get_by_ticker", "AAPL")]

    async def test_an_unknown_ticker_is_a_not_found_error(self) -> None:
        service, _, _ = build_service(make_stock(ticker_symbol="AAPL"))

        with pytest.raises(NotFoundError) as caught:
            await service.get_stock_by_ticker(ticker="ZZZZ")

        assert caught.value.code == "not_found"
        assert caught.value.details == {"resource": RESOURCE, "identifier": "ZZZZ"}

    async def test_the_error_reports_the_canonical_spelling(self) -> None:
        """So a caller can see the lookup was not simply a casing mistake."""
        service, _, _ = build_service()

        with pytest.raises(NotFoundError) as caught:
            await service.get_stock_by_ticker(ticker=" zzzz ")

        assert caught.value.details["identifier"] == "ZZZZ"
        assert "ZZZZ" in caught.value.message

    async def test_a_lookup_commits_nothing(self) -> None:
        service, _, session = build_service(make_stock())

        await service.get_stock_by_ticker(ticker="aapl")

        assert (session.commits, session.rollbacks) == (0, 0)


class TestNormaliseTicker:
    """The rule itself, in isolation — it is exported because ANV-22's ingest needs it too."""

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("aapl", "AAPL"),
            ("AAPL", "AAPL"),
            ("  aapl  ", "AAPL"),
            ("brk.b", "BRK.B"),
            ("btc-usd", "BTC-USD"),
            ("", ""),
            ("   ", ""),
        ],
    )
    def test_it_trims_and_upper_cases(self, given: str, expected: str) -> None:
        assert normalise_ticker(given) == expected

    def test_it_is_idempotent(self) -> None:
        assert normalise_ticker(normalise_ticker(" aapl ")) == normalise_ticker(" aapl ")


# ---------------------------------------------------------------------------------------
# scope
# ---------------------------------------------------------------------------------------


class TestReadOnly:
    """ANV-13 is deliberately a read-only ticket; ANV-22's ingest is what creates a stock."""

    def test_the_service_exposes_no_write_use_case(self) -> None:
        public = {
            name
            for name in dir(StockService)
            if not name.startswith("_") and callable(getattr(StockService, name))
        }

        assert public == {"get_stock", "get_stock_by_ticker", "list_stocks"}


# ---------------------------------------------------------------------------------------
# layering
# ---------------------------------------------------------------------------------------


def service_tree() -> ast.Module:
    return ast.parse(FilePath(stock_service_module.__file__).read_text(encoding="utf-8"))


class TestLayering:
    """`CLAUDE.md` §3, checked rather than trusted — prose conventions get broken."""

    def test_the_service_imports_no_web_framework(self) -> None:
        modules: set[str] = set()
        for node in ast.walk(service_tree()):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)
        roots = {module.split(".")[0] for module in modules}

        assert "fastapi" not in roots
        assert "starlette" not in roots
        assert "app.api" not in modules
        assert "app.deps" not in modules

    def test_the_service_raises_no_http_exception(self) -> None:
        used = {
            node.id if isinstance(node, ast.Name) else node.attr
            for node in ast.walk(service_tree())
            if isinstance(node, ast.Name | ast.Attribute)
        }
        assert "HTTPException" not in used

    def test_every_error_it_raises_is_a_domain_error(self) -> None:
        raised = {
            node.exc.func.id
            for node in ast.walk(service_tree())
            if isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
        }
        assert raised
        for name in raised:
            assert issubclass(getattr(stock_service_module, name), AnvexError), name

    def test_no_sqlalchemy_query_is_written_here(self) -> None:
        """`CLAUDE.md` §3: if you typed `select(` outside `app/repos/`, it is the wrong file."""
        source = FilePath(stock_service_module.__file__).read_text(encoding="utf-8")
        assert "select(" not in source
