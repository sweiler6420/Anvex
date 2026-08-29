"""Behaviour of ``app/clients/newsapi.py`` against a ``respx``-mocked vendor.

``CLAUDE.md`` §6 puts client tests in ``tests/integration/`` because a real
``httpx.AsyncClient`` is exercised end to end, but this module asks for no ``db_*`` fixture,
so it is not marked ``db`` and **runs unchanged with Docker stopped**.

**Nothing here has ever touched NewsAPI.** Every payload below is *hand-built* from the
vendor's published response shape — the ``status``/``totalResults``/``articles`` envelope,
the nested ``source`` object, the ``urlToImage``/``publishedAt`` camel-cased field names, and
the ``{"status": "error", "code": …, "message": …}`` refusal — with the field values chosen
to make an assertion sharp rather than copied from a response. They are not captured traffic.
No API key is configured anywhere in this repository, ``mock_http`` refuses to let a request
escape to the network, and the key strings below are inventions of this file.

The interesting halves of this module are the two things the base class cannot see:

* a **refusal that arrives as ``200 OK``**. NewsAPI sends its error envelope with a 4xx
  sometimes and with a 200 the rest of the time, and to
  :class:`~app.clients.base.BaseHTTPClient` a 2xx carrying valid JSON is a good response. The
  tests below prove the parser catches it, and that the resulting error is the *same* error
  the 4xx path produces — same code, same message, same ``details["reason"]`` — differing
  only in the keys that genuinely do not exist when no request was retried.
* **no key configured at all**, which is the state of every fresh clone. That must not become
  a round trip and a confusing ``client_error``; ``TestNoKeyConfigured`` asserts it is a
  precise, self-describing failure that never leaves the process.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import AsyncIterator, Iterable
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr, ValidationError
from structlog.testing import capture_logs

from app.clients.base import REDACTED, Failure
from app.clients.newsapi import (
    API_KEY_HEADER,
    API_KEY_SETTING,
    ERROR_CODE_FAILURES,
    EVERYTHING_PATH,
    MAX_PAGE_SIZE,
    NOT_CONFIGURED,
    TOP_HEADLINES_PATH,
    NewsApiClient,
    NewsArticle,
    NewsFeed,
    NewsSource,
)
from app.domain.errors import AnvexError, ExternalServiceError
from app.settings import Settings

#: The credential that must never turn up in a log line, a URL or an exception. Invented.
API_KEY = "newsapi-test-key-4242"

HOST = "https://newsapi.org"
TOP_URL = f"{HOST}{TOP_HEADLINES_PATH}"
EVERYTHING_URL = f"{HOST}{EVERYTHING_PATH}"

# ---------------------------------------------------------------------------------------
# Hand-built payloads
#
# Written from NewsAPI's documented response shape. Note what is null: `author`,
# `description`, `urlToImage` and `content` are null on real wire copy far more often than
# not, and the old router's own pasted blob has three of the four null on most of its items.
# The model has to survive that, and the domain rules have to rank around it.
# ---------------------------------------------------------------------------------------

REUTERS_ARTICLE: dict[str, Any] = {
    "source": {"id": "reuters", "name": "Reuters"},
    "author": "Reuters",
    "title": "Congress passes $886 billion defense policy bill - Reuters",
    "description": None,
    "url": "https://www.reuters.com/world/us/defense-policy-bill-2023-12-14/",
    "urlToImage": None,
    "publishedAt": "2023-12-14T22:09:00Z",
    "content": None,
}

WIRED_ARTICLE: dict[str, Any] = {
    "source": {"id": "wired", "name": "Wired"},
    "author": "Will Knight",
    "title": "A Plan for Keeping Super-Intelligent AI in Check - WIRED",
    "description": "The Superalignment team has devised a way to guide model behaviour.",
    "url": "https://www.wired.com/story/openai-ilya-sutskever-ai-safety/",
    "urlToImage": "https://media.wired.com/photos/657a4dc7/lead.jpg",
    "publishedAt": "2023-12-14T18:44:45Z",
    "content": "OpenAI was founded on a promise… [+2541 chars]",
}

#: An outlet NewsAPI has no slug for — ``source.id`` is null and only the name is set. Both
#: shapes appear side by side in a single real response.
UNSLUGGED_ARTICLE: dict[str, Any] = {
    "source": {"id": None, "name": "Science.org"},
    "author": "Science",
    "title": "Breakthrough of the Year: weight loss drugs - Science",
    "description": "A real shot at fighting obesity.",
    "url": "https://www.science.org/content/article/breakthrough-of-the-year-2023",
    "urlToImage": None,
    "publishedAt": "2023-12-14T19:00:00Z",
    "content": None,
}

ARTICLES: list[dict[str, Any]] = [REUTERS_ARTICLE, WIRED_ARTICLE, UNSLUGGED_ARTICLE]


def feed_payload(
    articles: list[dict[str, Any]] | None = None,
    *,
    total: Any = 36,
    status: Any = "ok",
) -> dict[str, Any]:
    """A successful ``top-headlines`` / ``everything`` body, hand-built."""
    return {
        "status": status,
        "totalResults": total,
        "articles": ARTICLES if articles is None else articles,
    }


def error_payload(code: str, message: str = "Something the vendor said.") -> dict[str, Any]:
    """NewsAPI's refusal envelope. Sent with a 200 as often as with a 4xx."""
    return {"status": "error", "code": code, "message": message}


def an_article(overrides: dict[str, Any]) -> dict[str, Any]:
    """:data:`REUTERS_ARTICLE` with selected vendor fields replaced."""
    return {**REUTERS_ARTICLE, **overrides}


# ---------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """The shared fixture with one field pinned — ``CLAUDE.md`` §6's documented idiom."""
    return settings.model_copy(update={"newsapi_api_key": SecretStr(API_KEY)})


@pytest.fixture
def sleeps() -> list[float]:
    """Every delay the retry loop asked for, so a retry is asserted without waiting."""
    return []


@pytest.fixture
async def client(settings: Settings, sleeps: list[float]) -> AsyncIterator[NewsApiClient]:
    async def record(delay: float) -> None:
        sleeps.append(delay)

    vendor = NewsApiClient(settings, sleep=record, jitter=lambda: 0.0)
    try:
        yield vendor
    finally:
        await vendor.aclose()


@pytest.fixture
async def keyless(settings: Settings) -> AsyncIterator[NewsApiClient]:
    """A client on a fresh clone's settings: ``NEWSAPI_API_KEY`` blank."""
    vendor = NewsApiClient(settings.model_copy(update={"newsapi_api_key": SecretStr("")}))
    try:
        yield vendor
    finally:
        await vendor.aclose()


def details_of(caught: pytest.ExceptionInfo[ExternalServiceError]) -> dict[str, Any]:
    return caught.value.details


# ---------------------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------------------


class TestASuccessfulFetch:
    async def test_top_headlines_returns_a_typed_feed_never_a_response(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        route = mock_http.get(TOP_URL).respond(200, json=feed_payload())

        feed = await client.fetch_top_headlines()

        assert isinstance(feed, NewsFeed)
        assert route.call_count == 1
        assert len(feed.articles) == 3
        assert all(isinstance(article, NewsArticle) for article in feed.articles)

    async def test_everything_returns_a_typed_feed_too(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        route = mock_http.get(EVERYTHING_URL).respond(200, json=feed_payload())

        feed = await client.fetch_everything('"AAPL" OR "Apple Inc."')

        assert isinstance(feed, NewsFeed)
        assert route.call_count == 1
        assert len(feed.articles) == 3

    async def test_the_vendors_field_names_are_mapped_onto_the_models(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(TOP_URL).respond(200, json=feed_payload([WIRED_ARTICLE]))

        article = (await client.fetch_top_headlines()).articles[0]

        assert article == NewsArticle(
            source=NewsSource(id="wired", name="Wired"),
            author="Will Knight",
            title="A Plan for Keeping Super-Intelligent AI in Check - WIRED",
            description="The Superalignment team has devised a way to guide model behaviour.",
            url="https://www.wired.com/story/openai-ilya-sutskever-ai-safety/",
            url_to_image="https://media.wired.com/photos/657a4dc7/lead.jpg",
            published_at=dt.datetime(2023, 12, 14, 18, 44, 45, tzinfo=dt.UTC),
            content="OpenAI was founded on a promise… [+2541 chars]",
        )

    async def test_a_published_timestamp_is_timezone_aware(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        """The domain ranks on this. A naive value would compare against ``now`` by raising."""
        mock_http.get(TOP_URL).respond(200, json=feed_payload())

        feed = await client.fetch_top_headlines()

        assert all(article.published_at is not None for article in feed.articles)
        assert all(
            article.published_at.utcoffset() == dt.timedelta(0)  # type: ignore[union-attr]
            for article in feed.articles
        )

    async def test_the_outlet_is_reachable_without_digging_through_the_source_object(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        """``app/domain/news.py`` ranks on the outlet and must not reach into a nested dict."""
        mock_http.get(TOP_URL).respond(200, json=feed_payload([REUTERS_ARTICLE]))

        article = (await client.fetch_top_headlines()).articles[0]

        assert article.source_name == "Reuters"
        assert article.source.id == "reuters"

    async def test_an_outlet_without_a_slug_is_not_a_failure(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        """``source.id`` is null for everything outside NewsAPI's own partner list."""
        mock_http.get(TOP_URL).respond(200, json=feed_payload([UNSLUGGED_ARTICLE]))

        article = (await client.fetch_top_headlines()).articles[0]

        assert article.source.id is None
        assert article.source_name == "Science.org"

    async def test_the_nulls_the_vendor_actually_sends_are_carried_as_none(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        """Four fields are routinely null upstream. None of them is an upstream failure."""
        mock_http.get(TOP_URL).respond(200, json=feed_payload([REUTERS_ARTICLE]))

        article = (await client.fetch_top_headlines()).articles[0]

        assert article.description is None
        assert article.url_to_image is None
        assert article.content is None
        assert article.title is not None

    async def test_the_vendors_ordering_is_preserved(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        """Ranking is a transformation, and it belongs to ``app/domain/news.py``."""
        mock_http.get(TOP_URL).respond(200, json=feed_payload())

        feed = await client.fetch_top_headlines()

        assert [article.url for article in feed.articles] == [row["url"] for row in ARTICLES]

    async def test_the_vendors_match_count_is_carried(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(TOP_URL).respond(200, json=feed_payload(total=1_284))

        assert (await client.fetch_top_headlines()).total_results == 1_284

    async def test_an_empty_result_set_is_a_result_not_an_error(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        """A symbol nobody wrote about this week is an answer. The service decides what to
        do with it — and it cannot decide anything if this raises."""
        mock_http.get(EVERYTHING_URL).respond(200, json=feed_payload([], total=0))

        feed = await client.fetch_everything('"QUIET"')

        assert feed.articles == ()
        assert feed.total_results == 0

    async def test_a_nonsense_total_falls_back_to_what_actually_arrived(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        """The count is a paging convenience. An intact article list is not discarded
        because the integer beside it was not an integer."""
        mock_http.get(TOP_URL).respond(200, json=feed_payload(total="many"))

        assert (await client.fetch_top_headlines()).total_results == 3

    async def test_a_negative_total_is_floored_rather_than_reported(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(TOP_URL).respond(200, json=feed_payload(total=-5))

        assert (await client.fetch_top_headlines()).total_results == 0


# ---------------------------------------------------------------------------------------
# the request
# ---------------------------------------------------------------------------------------


class TestTheRequest:
    async def test_top_headlines_sends_the_documented_parameters(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        route = mock_http.get(TOP_URL).respond(200, json=feed_payload())

        await client.fetch_top_headlines(country="gb", category="business", page_size=25, page=2)

        query = dict(route.calls.last.request.url.params)
        assert query == {"country": "gb", "category": "business", "pageSize": "25", "page": "2"}

    async def test_no_category_means_no_category_parameter(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        """Which slice of the news an investment app shows is a product decision, and
        defaulting it here would hide that decision inside a vendor module."""
        route = mock_http.get(TOP_URL).respond(200, json=feed_payload())

        await client.fetch_top_headlines()

        query = dict(route.calls.last.request.url.params)
        assert "category" not in query
        assert query["country"] == "us"
        assert query["pageSize"] == str(MAX_PAGE_SIZE)

    async def test_everything_sends_the_query_verbatim(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        """Composing a query out of a ticker is ``app/domain/news.py``'s rule, not this
        layer's — so whatever the service built arrives unchanged."""
        route = mock_http.get(EVERYTHING_URL).respond(200, json=feed_payload())

        await client.fetch_everything('"CAT" OR "Caterpillar Inc."')

        query = dict(route.calls.last.request.url.params)
        assert query["q"] == '"CAT" OR "Caterpillar Inc."'
        assert query["sortBy"] == "publishedAt"
        assert query["language"] == "en"

    async def test_every_language_means_no_language_parameter(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        route = mock_http.get(EVERYTHING_URL).respond(200, json=feed_payload())

        await client.fetch_everything('"AAPL"', language=None)

        assert "language" not in dict(route.calls.last.request.url.params)

    async def test_an_over_large_page_size_is_passed_through_not_clamped(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        """The vendor owns its own ceiling and says so with a ``parameterInvalid``. Silently
        shrinking the request would hide that from whoever wrote the bad call."""
        route = mock_http.get(TOP_URL).respond(200, json=feed_payload())

        await client.fetch_top_headlines(page_size=500)

        assert dict(route.calls.last.request.url.params)["pageSize"] == "500"

    async def test_nothing_here_ever_sleeps_to_stay_under_a_quota(
        self, client: NewsApiClient, mock_http: respx.MockRouter, sleeps: list[float]
    ) -> None:
        """``CLAUDE.md`` §3: proactive throttling belongs to the job that fans out."""
        mock_http.get(TOP_URL).respond(200, json=feed_payload())
        mock_http.get(EVERYTHING_URL).respond(200, json=feed_payload())

        await client.fetch_top_headlines()
        await client.fetch_everything('"AAPL"')

        assert sleeps == []


# ---------------------------------------------------------------------------------------
# the credential — a header, not a query parameter
# ---------------------------------------------------------------------------------------


class TestTheCredentialTravelsInAHeader:
    """The decision this module made differently from ANV-18: NewsAPI accepts the key either
    way, so it takes the way the base never writes down.
    """

    async def test_the_key_is_sent_as_a_header(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        route = mock_http.get(TOP_URL).respond(200, json=feed_payload())

        await client.fetch_top_headlines()

        assert route.calls.last.request.headers[API_KEY_HEADER] == API_KEY

    async def test_the_key_is_not_in_the_url_at_all(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        """Not redacted in the log — genuinely absent from the request line, so a proxy log
        or a ``Referer`` never carries it either."""
        route = mock_http.get(TOP_URL).respond(200, json=feed_payload())

        await client.fetch_top_headlines()

        url = str(route.calls.last.request.url)
        assert API_KEY not in url
        assert "apiKey" not in url

    async def test_it_stays_a_secret_on_the_client(self, client: NewsApiClient) -> None:
        """The base unwraps it per request and stores no plaintext — ``CLAUDE.md`` §3."""
        assert isinstance(client._api_key, SecretStr)
        assert API_KEY not in repr(client._api_key)


# ---------------------------------------------------------------------------------------
# a refusal that arrives as 200 OK
# ---------------------------------------------------------------------------------------


class TestAnErrorBodyAtTwoHundred:
    """The reason this module has a parser at all. To the base a 2xx with valid JSON is a
    good response, so every one of these would otherwise be reported as success.
    """

    @pytest.fixture(autouse=True)
    def _route(self, mock_http: respx.MockRouter) -> respx.Route:
        return mock_http.get(TOP_URL)

    @pytest.mark.parametrize(("code", "failure"), sorted(ERROR_CODE_FAILURES.items()))
    async def test_every_documented_code_maps_to_its_failure(
        self,
        client: NewsApiClient,
        _route: respx.Route,
        code: str,
        failure: Failure,
    ) -> None:
        _route.respond(200, json=error_payload(code))

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert details_of(caught)["reason"] == failure.value

    async def test_a_rate_limit_is_reported_as_one(
        self, client: NewsApiClient, _route: respx.Route
    ) -> None:
        _route.respond(200, json=error_payload("rateLimited"))

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert details_of(caught)["reason"] == "rate_limited"

    async def test_an_exhausted_key_is_a_rate_limit_because_waiting_fixes_it(
        self, client: NewsApiClient, _route: respx.Route
    ) -> None:
        _route.respond(200, json=error_payload("apiKeyExhausted"))

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert details_of(caught)["reason"] == "rate_limited"

    async def test_the_results_ceiling_is_not_a_rate_limit_because_waiting_never_fixes_it(
        self, client: NewsApiClient, _route: respx.Route
    ) -> None:
        """``maximumResultsReached`` reads like a quota and is not one: the requested page
        lies past the plan's hard ceiling and will be refused at any hour of any day. Filing
        it as ``rate_limited`` would hand ANV-22 a reschedule signal for a loop.
        """
        _route.respond(200, json=error_payload("maximumResultsReached"))

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert details_of(caught)["reason"] == "client_error"

    async def test_the_vendor_admitting_fault_is_a_server_error(
        self, client: NewsApiClient, _route: respx.Route
    ) -> None:
        _route.respond(200, json=error_payload("unexpectedError"))

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert details_of(caught)["reason"] == "server_error"

    async def test_a_code_nobody_has_seen_before_is_a_refusal_not_a_retry(
        self, client: NewsApiClient, _route: respx.Route
    ) -> None:
        """Treating an unrecognised code as retryable would triple the cost of every future
        error NewsAPI invents."""
        _route.respond(200, json=error_payload("someFutureCode"))

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert details_of(caught)["reason"] == "client_error"

    async def test_an_error_with_no_code_at_all_is_still_a_refusal(
        self, client: NewsApiClient, _route: respx.Route
    ) -> None:
        _route.respond(200, json={"status": "error", "message": "no code here"})

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert details_of(caught)["reason"] == "client_error"

    async def test_it_carries_no_invented_attempt_count(
        self, client: NewsApiClient, _route: respx.Route
    ) -> None:
        """ANV-18's rule: the retry loop had already succeeded, so there is no attempt count
        belonging to this failure and a fabricated ``1`` would be worse than an absent key."""
        _route.respond(200, json=error_payload("rateLimited"))

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert "attempts" not in details_of(caught)
        assert "status_code" not in details_of(caught)

    async def test_the_call_is_not_repeated(
        self, client: NewsApiClient, _route: respx.Route, sleeps: list[float]
    ) -> None:
        _route.respond(200, json=error_payload("rateLimited"))

        with pytest.raises(ExternalServiceError):
            await client.fetch_top_headlines()

        assert _route.call_count == 1
        assert sleeps == []

    async def test_the_vendors_wording_is_never_forwarded(
        self, client: NewsApiClient, _route: respx.Route
    ) -> None:
        """``CLAUDE.md`` §4 makes ``details`` a public contract; upstream output stays out."""
        secret_ish = "Your key 0123456789abcdef is invalid or incorrect."
        _route.respond(200, json=error_payload("apiKeyInvalid", secret_ish))

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert secret_ish not in str(caught.value)
        assert "0123456789abcdef" not in json.dumps(details_of(caught), default=str)

    async def test_both_endpoints_check_the_body(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(EVERYTHING_URL).respond(200, json=error_payload("parameterInvalid"))

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_everything('"AAPL"')

        assert details_of(caught)["reason"] == "client_error"


# ---------------------------------------------------------------------------------------
# the same refusal, arriving as a 4xx
# ---------------------------------------------------------------------------------------


class TestTheSameErrorBodyAtAFourHundred:
    """NewsAPI sends the identical envelope with a real status code much of the time. Then
    the **base** classifies it from the status line and this module never sees the body — and
    the two paths have to agree, or a consumer would have to branch on which one it got.
    """

    async def test_a_401_is_a_client_error_from_the_status_line(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        route = mock_http.get(TOP_URL).respond(401, json=error_payload("apiKeyInvalid"))

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert details_of(caught)["reason"] == "client_error"
        assert details_of(caught)["status_code"] == 401
        assert route.call_count == 1  # a 4xx is never retried

    async def test_a_429_is_a_rate_limit_and_is_retried_once(
        self, client: NewsApiClient, mock_http: respx.MockRouter, sleeps: list[float]
    ) -> None:
        route = mock_http.get(TOP_URL).respond(429, json=error_payload("rateLimited"))

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert details_of(caught)["reason"] == "rate_limited"
        assert details_of(caught)["attempts"] == 2
        assert route.call_count == 2
        assert len(sleeps) == 1

    async def test_a_body_detected_rate_limit_reads_identically_to_a_429(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        """The property a ``_check_payload`` hook on the base would have existed to give.
        It is already given, by ``_error`` — which is why no hook was added: see the module
        docstring of ``app/clients/newsapi.py``.
        """
        mock_http.get(TOP_URL).respond(200, json=error_payload("rateLimited"))
        with pytest.raises(ExternalServiceError) as from_body:
            await client.fetch_top_headlines()

        mock_http.get(TOP_URL).respond(429, json=error_payload("rateLimited"))
        with pytest.raises(ExternalServiceError) as from_status:
            await client.fetch_top_headlines()

        assert from_body.value.message == from_status.value.message
        assert from_body.value.code == from_status.value.code
        assert details_of(from_body)["reason"] == details_of(from_status)["reason"]
        assert details_of(from_body)["service"] == details_of(from_status)["service"]
        # The only difference: keys that genuinely do not exist for a body-detected failure.
        assert set(details_of(from_status)) - set(details_of(from_body)) == {
            "attempts",
            "status_code",
        }

    async def test_a_500_is_retried_by_the_base(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        route = mock_http.get(TOP_URL).respond(500, json=error_payload("unexpectedError"))

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert details_of(caught)["reason"] == "server_error"
        assert route.call_count == 3


# ---------------------------------------------------------------------------------------
# malformed payloads
# ---------------------------------------------------------------------------------------


class TestAMalformedPayload:
    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("a bare list", []),
            ("a bare string", "ok"),
            ("a number", 7),
            ("null", None),
        ],
    )
    async def test_a_body_that_is_not_an_object_is_malformed(
        self, client: NewsApiClient, mock_http: respx.MockRouter, label: str, payload: Any
    ) -> None:
        mock_http.get(TOP_URL).respond(200, json=payload)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert details_of(caught)["reason"] == "malformed_response", label

    @pytest.mark.parametrize("status", ["", "OK", "pending", 1, None])
    async def test_a_status_that_is_neither_ok_nor_error_is_malformed(
        self, client: NewsApiClient, mock_http: respx.MockRouter, status: Any
    ) -> None:
        """Not a NewsAPI envelope at all. Guessing what it meant would be the repair
        ``CLAUDE.md`` §3 forbids."""
        mock_http.get(TOP_URL).respond(200, json=feed_payload(status=status))

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert details_of(caught)["reason"] == "malformed_response"

    @pytest.mark.parametrize("articles", [{}, "none", 3, None])
    async def test_articles_that_are_not_a_list_are_malformed(
        self, client: NewsApiClient, mock_http: respx.MockRouter, articles: Any
    ) -> None:
        mock_http.get(TOP_URL).respond(
            200, json={"status": "ok", "totalResults": 0, "articles": articles}
        )

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert details_of(caught)["reason"] == "malformed_response"

    async def test_an_article_that_is_not_an_object_fails_the_whole_feed(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(TOP_URL).respond(200, json=feed_payload([REUTERS_ARTICLE, "nope"]))  # type: ignore[list-item]

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert details_of(caught)["reason"] == "malformed_response"

    @pytest.mark.parametrize(
        "published",
        ["yesterday", "2023-13-45T99:99:99Z", "14/12/2023", 1702591740, ["2023-12-14"]],
    )
    async def test_a_present_but_unreadable_timestamp_is_malformed(
        self, client: NewsApiClient, mock_http: respx.MockRouter, published: Any
    ) -> None:
        """The one field this parser is strict about: the domain ranks on it, and an article
        an hour out of place is a wrong answer that looks exactly like a right one."""
        mock_http.get(TOP_URL).respond(
            200, json=feed_payload([an_article({"publishedAt": published})])
        )

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert details_of(caught)["reason"] == "malformed_response"

    async def test_a_naive_timestamp_is_refused_rather_than_assumed_to_be_utc(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        """Assuming would be a silent repair, on the field everything is ordered by."""
        mock_http.get(TOP_URL).respond(
            200, json=feed_payload([an_article({"publishedAt": "2023-12-14T22:09:00"})])
        )

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert details_of(caught)["reason"] == "malformed_response"

    @pytest.mark.parametrize("published", [None, "", "   "])
    async def test_an_absent_timestamp_is_none_not_a_failure(
        self, client: NewsApiClient, mock_http: respx.MockRouter, published: Any
    ) -> None:
        """Absence is a fact; a broken ISO-8601 string is a break. Whether an undated article
        is *usable* is ``app/domain/news.py``'s question, not this layer's."""
        mock_http.get(TOP_URL).respond(
            200, json=feed_payload([an_article({"publishedAt": published})])
        )

        feed = await client.fetch_top_headlines()

        assert feed.articles[0].published_at is None

    async def test_an_offset_other_than_utc_is_kept(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(TOP_URL).respond(
            200, json=feed_payload([an_article({"publishedAt": "2023-12-14T22:09:00+02:00"})])
        )

        published = (await client.fetch_top_headlines()).articles[0].published_at

        assert published == dt.datetime(
            2023, 12, 14, 22, 9, tzinfo=dt.timezone(dt.timedelta(hours=2))
        )

    @pytest.mark.parametrize("field", ["title", "url", "author", "description", "urlToImage"])
    async def test_a_missing_optional_field_is_none_rather_than_a_failure(
        self, client: NewsApiClient, mock_http: respx.MockRouter, field: str
    ) -> None:
        """NewsAPI really does send these as null, including ``title`` and ``url`` on a
        withdrawn item. Deciding such an article is unusable is a domain rule."""
        row = {name: value for name, value in REUTERS_ARTICLE.items() if name != field}
        mock_http.get(TOP_URL).respond(200, json=feed_payload([row]))

        feed = await client.fetch_top_headlines()

        assert len(feed.articles) == 1

    async def test_a_source_that_is_not_an_object_degrades_rather_than_fails(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(TOP_URL).respond(200, json=feed_payload([an_article({"source": "Reuters"})]))

        article = (await client.fetch_top_headlines()).articles[0]

        assert article.source_name is None

    async def test_a_malformed_payload_is_not_retried(
        self, client: NewsApiClient, mock_http: respx.MockRouter, sleeps: list[float]
    ) -> None:
        """A vendor answering with the wrong shape is broken, not blipping."""
        route = mock_http.get(TOP_URL).respond(200, json=feed_payload(status="???"))

        with pytest.raises(ExternalServiceError):
            await client.fetch_top_headlines()

        assert route.call_count == 1
        assert sleeps == []

    async def test_a_body_that_is_not_json_is_the_bases_problem(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(TOP_URL).respond(200, text="<html>maintenance</html>")

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert details_of(caught)["reason"] == "malformed_response"

    async def test_a_transport_failure_is_still_the_layers_one_exception(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(TOP_URL).mock(side_effect=httpx.ConnectError("no route to host"))

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        assert details_of(caught)["reason"] == "transport_error"

    @pytest.mark.parametrize(
        "payload",
        [
            [],
            {"status": "???"},
            error_payload("apiKeyInvalid"),
            feed_payload([an_article({"publishedAt": "not a date"})]),
            feed_payload(["an article that is a string"]),  # type: ignore[list-item]
        ],
    )
    async def test_every_failure_is_an_anvex_error_and_nothing_else_escapes(
        self, client: NewsApiClient, mock_http: respx.MockRouter, payload: Any
    ) -> None:
        """The layer promises exactly one exception type. A ``ValidationError`` or an
        ``AttributeError`` reaching a service would break that promise."""
        mock_http.get(TOP_URL).respond(200, json=payload)

        with pytest.raises(AnvexError):
            await client.fetch_top_headlines()


# ---------------------------------------------------------------------------------------
# no key configured — the default state of a fresh clone
# ---------------------------------------------------------------------------------------


class TestNoKeyConfigured:
    """``NEWSAPI_API_KEY`` is blank in ``.env.example``, so this is not an edge case, it is
    what happens to everybody until somebody signs up for a key.
    """

    async def test_it_never_reaches_the_network(
        self, keyless: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        """A keyless call would spend a round trip to be told ``apiKeyMissing``. Worse, it
        would arrive as ``client_error`` — indistinguishable from a malformed query."""
        route = mock_http.get(TOP_URL).respond(200, json=feed_payload())

        with pytest.raises(ExternalServiceError):
            await keyless.fetch_top_headlines()

        assert route.call_count == 0

    async def test_it_says_what_is_wrong_and_what_to_set(
        self, keyless: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        """Obvious from the response body, with no log-reading required."""
        mock_http.get(TOP_URL).respond(200, json=feed_payload())

        with pytest.raises(ExternalServiceError) as caught:
            await keyless.fetch_top_headlines()

        assert details_of(caught) == {
            "service": "newsapi",
            "reason": NOT_CONFIGURED,
            "setting": API_KEY_SETTING,
        }
        assert "not configured" in caught.value.message

    async def test_both_operations_refuse(
        self, keyless: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        route = mock_http.get(EVERYTHING_URL).respond(200, json=feed_payload())

        with pytest.raises(ExternalServiceError) as caught:
            await keyless.fetch_everything('"AAPL"')

        assert details_of(caught)["reason"] == NOT_CONFIGURED
        assert route.call_count == 0

    @pytest.mark.parametrize("value", ["", "   ", "\t\n"])
    def test_whitespace_is_not_a_key(self, settings: Settings, value: str) -> None:
        vendor = NewsApiClient(settings.model_copy(update={"newsapi_api_key": SecretStr(value)}))

        assert vendor.is_configured is False

    def test_a_real_key_is_configured(self, client: NewsApiClient) -> None:
        assert client.is_configured is True

    def test_construction_itself_never_fails(self, settings: Settings) -> None:
        """The dependency factory builds one long before any route calls it."""
        vendor = NewsApiClient(settings.model_copy(update={"newsapi_api_key": SecretStr("")}))

        assert vendor.auth_headers()[API_KEY_HEADER].get_secret_value() == ""  # type: ignore[union-attr]


# ---------------------------------------------------------------------------------------
# the key never escapes
# ---------------------------------------------------------------------------------------


class TestTheApiKeyNeverEscapes:
    """The old ``news.py`` had a live key committed in a comment. Nothing resembling that is
    possible here, and these assert it stays impossible.
    """

    async def test_no_log_line_from_a_successful_fetch_contains_the_key(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(TOP_URL).respond(200, json=feed_payload())

        with capture_logs() as entries:
            await client.fetch_top_headlines(category="business")

        assert entries
        assert API_KEY not in json.dumps(entries, default=str)

    async def test_the_logged_url_is_still_diagnostic(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        """Headers are never logged at all, so there is nothing to redact — and the query,
        which *is* logged, keeps everything that makes the line worth having."""
        mock_http.get(TOP_URL).respond(200, json=feed_payload())

        with capture_logs() as entries:
            await client.fetch_top_headlines(category="business")

        logged = json.dumps(entries, default=str)
        assert "category=business" in logged
        assert "country=us" in logged
        assert REDACTED not in logged  # nothing needed blanking in the first place

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("rate limited", error_payload("rateLimited")),
            ("bad key", error_payload("apiKeyInvalid")),
            ("malformed", {"status": "???"}),
        ],
    )
    async def test_the_200_failure_paths_never_log_the_key(
        self,
        client: NewsApiClient,
        mock_http: respx.MockRouter,
        label: str,
        payload: dict[str, Any],
    ) -> None:
        mock_http.get(TOP_URL).respond(200, json=payload)

        with capture_logs() as entries, pytest.raises(ExternalServiceError):
            await client.fetch_top_headlines()

        assert entries
        assert API_KEY not in json.dumps(entries, default=str), label

    @pytest.mark.parametrize(
        ("status", "payload"),
        [
            (200, error_payload("rateLimited")),
            (200, error_payload("apiKeyInvalid")),
            (200, {"status": "???"}),
            (401, error_payload("apiKeyInvalid")),
            (429, error_payload("rateLimited")),
        ],
    )
    async def test_the_exception_carries_nothing_derived_from_the_key(
        self,
        client: NewsApiClient,
        mock_http: respx.MockRouter,
        status: int,
        payload: dict[str, Any],
    ) -> None:
        """``details`` is serialised straight to an API consumer — ``CLAUDE.md`` §4."""
        mock_http.get(TOP_URL).respond(status, json=payload)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_top_headlines()

        error = caught.value
        assert API_KEY not in str(error)
        assert API_KEY not in repr(error)
        assert API_KEY not in json.dumps(error.details, default=str)

    async def test_a_transport_error_message_is_scrubbed_too(
        self, client: NewsApiClient, mock_http: respx.MockRouter
    ) -> None:
        """The last line of defence: text this repo did not compose itself."""
        mock_http.get(TOP_URL).mock(
            side_effect=httpx.ConnectError(f"failed while sending {API_KEY}")
        )

        with capture_logs() as entries, pytest.raises(ExternalServiceError):
            await client.fetch_top_headlines()

        assert API_KEY not in json.dumps(entries, default=str)


# ---------------------------------------------------------------------------------------
# the parser, reached directly
# ---------------------------------------------------------------------------------------


class TestTheParserInIsolation:
    """The parsing is the whole of this module's judgement, so it is also tested with no
    socket in front of it — one fewer moving part between the assertion and the rule.
    """

    def _parse(self, settings: Settings, payload: Any) -> NewsFeed:
        return NewsApiClient(settings)._parse_feed(payload)

    def test_a_good_payload_parses(self, settings: Settings) -> None:
        assert len(self._parse(settings, feed_payload()).articles) == 3

    def test_an_error_body_raises_without_any_http(self, settings: Settings) -> None:
        with pytest.raises(ExternalServiceError) as caught:
            self._parse(settings, error_payload("rateLimited"))

        assert caught.value.details["reason"] == "rate_limited"

    def test_whitespace_around_a_value_is_trimmed(self, settings: Settings) -> None:
        feed = self._parse(
            settings,
            feed_payload([an_article({"title": "  Trimmed headline  ", "author": "  R  "})]),
        )

        assert feed.articles[0].title == "Trimmed headline"
        assert feed.articles[0].author == "R"

    def test_a_blank_string_is_the_same_as_absent(self, settings: Settings) -> None:
        feed = self._parse(settings, feed_payload([an_article({"description": "   "})]))

        assert feed.articles[0].description is None

    def test_a_non_string_where_a_string_belongs_degrades_to_none(self, settings: Settings) -> None:
        """Nullability here is the vendor's normal behaviour, so a wrong *type* on an
        optional field is flattened rather than failing a feed that is otherwise intact."""
        feed = self._parse(settings, feed_payload([an_article({"author": 42})]))

        assert feed.articles[0].author is None


# ---------------------------------------------------------------------------------------
# the layer's shape
# ---------------------------------------------------------------------------------------


class TestTheSubclassStaysSmall:
    """``CLAUDE.md`` §3: two class attributes, one credential, one method per operation. The
    AST sweep in ``tests/unit/test_clients_base.py`` guards the imports; these guard the shape.
    """

    def test_it_names_its_vendor_and_host(self) -> None:
        assert NewsApiClient.vendor == "newsapi"
        assert NewsApiClient.base_url == "https://newsapi.org"

    def test_the_article_model_is_the_clients_own_not_an_api_schema(self) -> None:
        """``app/schemas/`` is forbidden on purpose — a vendor does not share Anvex's public
        shape, and ``app/schemas/news.py`` is deliberately a different set of fields."""
        assert NewsArticle.__module__ == "app.clients.newsapi"
        fields: Iterable[str] = NewsArticle.model_fields
        assert set(fields) == {
            "source",
            "author",
            "title",
            "description",
            "url",
            "url_to_image",
            "published_at",
            "content",
        }

    def test_the_models_are_immutable(self) -> None:
        """A parsed vendor payload is a record of what was said, not a working buffer."""
        article = NewsArticle(title="x", url="https://example.com")

        with pytest.raises(ValidationError):
            article.title = "y"

    def test_it_writes_no_retry_loop_of_its_own(self) -> None:
        """``CLAUDE.md`` §3: if a vendor module has a ``while`` in it, the base is missing a
        feature."""
        from pathlib import Path

        import app.clients.newsapi as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "while " not in source
        assert "time.sleep" not in source
