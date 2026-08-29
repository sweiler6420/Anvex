"""``NewsService`` against an in-memory repo and a fake vendor client.

``tests/unit/`` (``CLAUDE.md`` §6): a service test belongs here when a fake answers the
question and in ``tests/integration/`` only when real SQL can. Nothing this service does
needs Postgres — it reads one row by a unique key — so all of it is here, at unit speed, with
Docker stopped.

The three things worth pinning down are the three judgement calls the module made:

* ``total`` counts **distinct** stories, not the vendor's ``totalResults``. The two numbers
  are genuinely different and the envelope has to be self-consistent, because ``offset``
  indexes into the de-duplicated list.
* ``by-symbol`` resolves the ticker **before** calling the vendor, so an unknown symbol is a
  404 and a known one produces a query built from the company name.
* the clock is read here and injected downward, which is what keeps ``app/domain/news.py``
  pure.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.clients.newsapi import MAX_PAGE_SIZE
from app.domain.errors import ExternalServiceError, NotFoundError
from app.schemas.news import NewsArticleOut
from app.schemas.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from app.services import stock as stock_service
from app.services import stock_data as stock_data_service
from app.services.news import RESOURCE, VENDOR_PAGE_SIZE, NewsService
from app.settings import Settings
from tests.helpers import FakeNewsApiClient, FakeStockRepo, StubSession, make_article, make_stock

NOW = dt.datetime(2026, 3, 2, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def apple() -> Any:
    return make_stock(ticker_symbol="AAPL", company="Apple Inc.")


@pytest.fixture
def stocks(apple: Any) -> FakeStockRepo:
    return FakeStockRepo(apple)


def build(client: FakeNewsApiClient, stocks: FakeStockRepo | None = None) -> NewsService:
    return NewsService(
        StubSession(),  # type: ignore[arg-type]
        Settings(),
        client=client,  # type: ignore[arg-type]
        stocks=stocks or FakeStockRepo(),  # type: ignore[arg-type]
    )


def articles(count: int, *, prefix: str = "Story") -> list[Any]:
    """``count`` genuinely distinct articles, newest first."""
    return [
        make_article(
            title=f"{prefix} number {n}",
            url=f"https://outlet.com/{prefix.lower()}-{n}",
            published_at=NOW - dt.timedelta(hours=n),
        )
        for n in range(count)
    ]


# ---------------------------------------------------------------------------------------
# the front page
# ---------------------------------------------------------------------------------------


class TestTopStories:
    async def test_it_returns_the_standard_envelope(self) -> None:
        service = build(FakeNewsApiClient(*articles(3)))

        page = await service.top_stories()

        assert page.total == 3
        assert page.offset == 0
        assert page.limit == DEFAULT_PAGE_LIMIT
        assert all(isinstance(item, NewsArticleOut) for item in page.items)

    async def test_the_vendor_is_asked_for_a_full_page(self) -> None:
        client = FakeNewsApiClient(*articles(2))
        service = build(client)

        await service.top_stories()

        operation, kwargs = client.calls[0]
        assert operation == "top_headlines"
        assert kwargs["page_size"] == VENDOR_PAGE_SIZE == MAX_PAGE_SIZE

    async def test_a_category_is_forwarded(self) -> None:
        client = FakeNewsApiClient(*articles(1))
        service = build(client)

        await service.top_stories(category="business")

        assert client.calls[0][1]["category"] == "business"

    async def test_no_category_forwards_none(self) -> None:
        client = FakeNewsApiClient(*articles(1))
        service = build(client)

        await service.top_stories()

        assert client.calls[0][1]["category"] is None

    async def test_an_empty_feed_is_an_empty_page_not_an_error(self) -> None:
        service = build(FakeNewsApiClient())

        page = await service.top_stories()

        assert page.items == []
        assert page.total == 0
        assert page.has_more is False

    async def test_a_vendor_failure_is_not_swallowed(self) -> None:
        """A 502 is the honest answer; an empty page would be a lie about the world."""
        boom = ExternalServiceError("newsapi", details={"reason": "rate_limited"})
        service = build(FakeNewsApiClient(error=boom))

        with pytest.raises(ExternalServiceError):
            await service.top_stories()


# ---------------------------------------------------------------------------------------
# what `total` counts — the paging decision
# ---------------------------------------------------------------------------------------


class TestTheCountIsHonest:
    async def test_total_counts_distinct_stories_not_the_vendors_match_count(self) -> None:
        """The vendor counts a story once per outlet that ran it. ``offset`` indexes into the
        de-duplicated list, so forwarding ``totalResults`` would make ``has_more`` lie.
        """
        duplicates = [
            make_article(title="One story - Reuters", url="https://reuters.com/x"),
            make_article(title="One story | CNBC", url="https://cnbc.com/y"),
            make_article(title="Another story", url="https://ft.com/z"),
        ]
        service = build(FakeNewsApiClient(*duplicates, total=3_412))

        page = await service.top_stories()

        assert page.total == 2
        assert len(page.items) == 2

    async def test_unusable_articles_are_not_counted_either(self) -> None:
        service = build(
            FakeNewsApiClient(
                make_article(title="Real", url="https://a.com/1"),
                make_article(title=None, url="https://a.com/2"),
                make_article(title="[Removed]", url="https://removed.com"),
                total=99,
            )
        )

        page = await service.top_stories()

        assert page.total == 1

    async def test_the_window_is_applied_to_the_deduplicated_list(self) -> None:
        service = build(FakeNewsApiClient(*articles(10)))

        page = await service.top_stories(limit=3, offset=3)

        assert page.total == 10
        assert page.limit == 3
        assert page.offset == 3
        assert [item.title for item in page.items] == [
            "Story number 3",
            "Story number 4",
            "Story number 5",
        ]
        assert page.has_more is True

    async def test_an_offset_past_the_end_is_an_empty_page_with_a_truthful_total(self) -> None:
        service = build(FakeNewsApiClient(*articles(4)))

        page = await service.top_stories(offset=50)

        assert page.items == []
        assert page.total == 4
        assert page.has_more is False

    async def test_the_last_page_says_so(self) -> None:
        service = build(FakeNewsApiClient(*articles(5)))

        page = await service.top_stories(limit=3, offset=3)

        assert len(page.items) == 2
        assert page.has_more is False

    async def test_an_over_large_limit_is_clamped_for_a_caller_with_no_request_to_reject(
        self,
    ) -> None:
        """The route rejects one with a 422; the clamp protects a job or a script."""
        service = build(FakeNewsApiClient(*articles(2)))

        page = await service.top_stories(limit=10_000)

        assert page.limit == MAX_PAGE_LIMIT

    async def test_a_negative_offset_is_floored(self) -> None:
        service = build(FakeNewsApiClient(*articles(2)))

        page = await service.top_stories(offset=-5)

        assert page.offset == 0


# ---------------------------------------------------------------------------------------
# by symbol
# ---------------------------------------------------------------------------------------


class TestForSymbol:
    async def test_a_known_ticker_returns_a_page(self, stocks: FakeStockRepo) -> None:
        service = build(FakeNewsApiClient(*articles(2)), stocks)

        page = await service.for_symbol(ticker="AAPL")

        assert page.total == 2

    async def test_the_query_is_built_from_the_ticker_and_the_company(
        self, stocks: FakeStockRepo
    ) -> None:
        """The real reason the ticker is resolved first: ``q="CAT"`` returns articles about
        cats, and the company name exists only in the stocks row."""
        client = FakeNewsApiClient(*articles(1))
        service = build(client, stocks)

        await service.for_symbol(ticker="AAPL")

        operation, kwargs = client.calls[0]
        assert operation == "everything"
        assert kwargs["query"] == '"AAPL" OR "Apple Inc."'

    async def test_the_ticker_is_normalised_by_this_layer(self, stocks: FakeStockRepo) -> None:
        """The repo lookup is exact and case-sensitive so it can use the unique index, and
        the API is not the only caller — so the rule lives here."""
        client = FakeNewsApiClient(*articles(1))
        service = build(client, stocks)

        await service.for_symbol(ticker="  aapl  ")

        assert client.calls[0][1]["query"] == '"AAPL" OR "Apple Inc."'

    async def test_an_unknown_ticker_is_a_not_found(self, stocks: FakeStockRepo) -> None:
        service = build(FakeNewsApiClient(*articles(1)), stocks)

        with pytest.raises(NotFoundError) as caught:
            await service.for_symbol(ticker="ZZZZ")

        assert caught.value.details == {"resource": RESOURCE, "identifier": "ZZZZ"}

    async def test_the_404_reports_the_canonical_spelling(self, stocks: FakeStockRepo) -> None:
        with pytest.raises(NotFoundError) as caught:
            await build(FakeNewsApiClient(), stocks).for_symbol(ticker="  zzzz ")

        assert caught.value.details["identifier"] == "ZZZZ"
        assert "'ZZZZ'" in caught.value.message

    async def test_an_unknown_ticker_never_reaches_the_vendor(self, stocks: FakeStockRepo) -> None:
        """A metered quota is not spent on a symbol Anvex already knows does not exist."""
        client = FakeNewsApiClient(*articles(1))

        with pytest.raises(NotFoundError):
            await build(client, stocks).for_symbol(ticker="ZZZZ")

        assert client.calls == []

    async def test_a_real_security_with_no_coverage_is_an_empty_page_not_a_404(
        self, stocks: FakeStockRepo
    ) -> None:
        """The distinction the vendor cannot make and the stocks table can: a typo versus a
        quiet week. ``CLAUDE.md`` §4 — a missing parent is a 404, an empty child is a 200."""
        service = build(FakeNewsApiClient(), stocks)

        page = await service.for_symbol(ticker="AAPL")

        assert page.items == []
        assert page.total == 0

    async def test_the_resource_noun_is_the_one_every_other_service_uses(self) -> None:
        """A client branching on ``details["resource"]`` sees one spelling whichever endpoint
        refused it."""
        assert RESOURCE == stock_service.RESOURCE == stock_data_service.RESOURCE == "stock"

    async def test_the_window_applies_here_too(self, stocks: FakeStockRepo) -> None:
        service = build(FakeNewsApiClient(*articles(6)), stocks)

        page = await service.for_symbol(ticker="AAPL", limit=2, offset=2)

        assert page.limit == 2
        assert page.offset == 2
        assert page.total == 6
        assert len(page.items) == 2


# ---------------------------------------------------------------------------------------
# the projection onto the public schema
# ---------------------------------------------------------------------------------------


class TestTheProjection:
    async def test_the_vendors_fields_reach_the_schema(self) -> None:
        service = build(
            FakeNewsApiClient(
                make_article(
                    title="Fed holds rates steady",
                    url="https://reuters.com/fed",
                    source_name="Reuters",
                    author="Howard Schneider",
                    description="No change to the target range.",
                    url_to_image="https://reuters.com/lead.jpg",
                    content="Truncated teaser… [+2541 chars]",
                    published_at=NOW,
                )
            )
        )

        item = (await service.top_stories()).items[0]

        assert item.title == "Fed holds rates steady"
        assert item.url == "https://reuters.com/fed"
        assert item.source_name == "Reuters"
        assert item.author == "Howard Schneider"
        assert item.description == "No change to the target range."
        assert item.url_to_image == "https://reuters.com/lead.jpg"
        assert item.published_at == NOW

    async def test_the_truncated_content_teaser_does_not_leave_the_building(self) -> None:
        """It is unreadable and re-serving a publisher's body text raises a licensing
        question this endpoint has no reason to raise. The client still parses it, because
        the domain counts its presence when choosing between two copies of one story."""
        service = build(FakeNewsApiClient(make_article(content="Truncated teaser… [+2541 chars]")))

        page = await service.top_stories()

        assert "content" not in NewsArticleOut.model_fields
        assert "2541 chars" not in page.model_dump_json()

    async def test_the_nulls_the_vendor_sends_survive_as_nulls(self) -> None:
        service = build(FakeNewsApiClient(make_article(description=None, url_to_image=None)))

        item = (await service.top_stories()).items[0]

        assert item.description is None
        assert item.url_to_image is None

    async def test_no_vendor_model_reaches_the_api(self) -> None:
        """``app/schemas/`` is the public shape and a vendor does not share it."""
        service = build(FakeNewsApiClient(*articles(2)))

        page = await service.top_stories()

        assert all(type(item) is NewsArticleOut for item in page.items)


# ---------------------------------------------------------------------------------------
# the clock
# ---------------------------------------------------------------------------------------


class TestTheClock:
    async def test_the_service_reads_it_and_the_domain_does_not(self) -> None:
        """``CLAUDE.md`` §4: the service is the only layer allowed to read a clock, and it
        passes an aware ``now`` down. Ranking against a real clock is what this proves —
        an article dated in the future must not outrank one from an hour ago.
        """
        recent = make_article(
            title="Happened an hour ago",
            url="https://a.com/recent",
            published_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=1),
        )
        embargoed = make_article(
            title="Dated next week",
            url="https://a.com/future",
            published_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=7),
        )
        service = build(FakeNewsApiClient(embargoed, recent))

        page = await service.top_stories()

        # Both clamp to a recency of 1.0 and neither is more complete, so the tie falls to
        # the later timestamp — deterministically, and never to the vendor's ordering.
        assert {item.title for item in page.items} == {
            "Happened an hour ago",
            "Dated next week",
        }
        assert page.items[0].title == "Dated next week"

    async def test_a_stale_article_ranks_below_a_fresh_one_against_the_real_clock(self) -> None:
        now = dt.datetime.now(dt.UTC)
        service = build(
            FakeNewsApiClient(
                make_article(
                    title="Last week",
                    url="https://a.com/old",
                    published_at=now - dt.timedelta(days=7),
                ),
                make_article(title="This hour", url="https://a.com/new", published_at=now),
            )
        )

        page = await service.top_stories()

        assert [item.title for item in page.items] == ["This hour", "Last week"]


# ---------------------------------------------------------------------------------------
# the dependency factories
# ---------------------------------------------------------------------------------------


class TestTheDependencyWiring:
    """``app/deps/news.py`` decides nothing, but it does own the client's lifetime — and a
    leaked connection pool is exactly the kind of thing nothing else would notice.
    """

    async def test_the_client_factory_closes_what_it_opened(self) -> None:
        from app.deps.news import get_newsapi_client

        generator = get_newsapi_client(Settings())
        client = await anext(generator)

        assert client.is_closed is False
        with pytest.raises(StopAsyncIteration):
            await anext(generator)
        assert client.is_closed is True

    def test_the_service_factory_wires_the_three_collaborators(self) -> None:
        from app.clients.newsapi import NewsApiClient
        from app.deps.news import get_news_service

        session = StubSession()
        settings = Settings()
        client = NewsApiClient(settings)

        service = get_news_service(session, settings, client)  # type: ignore[arg-type]

        assert isinstance(service, NewsService)
        assert service.session is session
        assert service.client is client
        # The repo is left to its keyword default: repos are stateless singletons.
        assert service.stocks is not None

    def test_a_service_is_not_constructible_without_a_client(self) -> None:
        """Keyword-only and required, unlike a repo — there is no module-level singleton to
        default to, because a client owns a connection pool and therefore a lifetime."""
        with pytest.raises(TypeError):
            NewsService(StubSession(), Settings())  # type: ignore[call-arg, arg-type]


# ---------------------------------------------------------------------------------------
# scope
# ---------------------------------------------------------------------------------------


class TestTheScopeBoundary:
    def test_it_reads_and_never_writes(self) -> None:
        """There is no ``NewsRepo`` and no ``news`` table: an article is a third-party
        document, and storing a copy buys a cache and a staleness problem."""
        public = {name for name, value in vars(NewsService).items() if not name.startswith("_")}

        assert public == {"top_stories", "for_symbol"}

    async def test_nothing_is_committed(self) -> None:
        session = StubSession()
        service = NewsService(
            session,  # type: ignore[arg-type]
            Settings(),
            client=FakeNewsApiClient(*articles(1)),  # type: ignore[arg-type]
            stocks=FakeStockRepo(),  # type: ignore[arg-type]
        )

        await service.top_stories()

        assert session.commits == 0

    def test_the_stock_repo_is_the_only_repo_it_holds(self) -> None:
        service = build(FakeNewsApiClient())
        repos = {
            name
            for name, value in vars(service).items()
            if name not in {"session", "settings", "client"}
        }

        assert repos == {"stocks"}
