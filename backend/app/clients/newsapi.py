"""NewsAPI — top headlines and full-text article search.

The vendor half of what the old ``AverageInvestorApi`` news router pretended to do. That
router was ~290 lines of December 2023 JSON pasted into a function body and returned
verbatim, behind an authentication check, with the one real line commented out beneath it
and a live API key sitting in that comment. Nothing of it is ported — there was nothing to
port. This module is the call it never made.

Two operations, because the vendor has two that matter:

* ``/v2/top-headlines`` — the front page, filtered by country and category.
* ``/v2/everything`` — full-text search across every indexed article.

Everything about *how* the request is made — timeouts, retry, redirect refusal, redaction,
the pooled ``httpx.AsyncClient``, and turning any failure into
:class:`~app.domain.errors.ExternalServiceError` — is inherited from
:class:`~app.clients.base.BaseHTTPClient`. What this module adds is the one thing the base
cannot know: what a NewsAPI payload means.

The credential travels in a **header**, not the query string
-------------------------------------------------------------

NewsAPI accepts the key either as ``apiKey=`` in the query or as an ``X-Api-Key`` request
header, and says so in its own documentation. This client sends the header, which is the
strictly safer of the two identical-looking options:

* ``CLAUDE.md`` §3 has the base log a **redacted URL** on every request and log **no headers
  at all**. A key in the query string is therefore protected by redaction; a key in a header
  is protected by never being written down in the first place. Redaction is good — ANV-17
  gave it two independent tests — but "not present" beats "blanked out" every time.
* A URL escapes in ways a header does not: an upstream proxy's access log, a ``Referer``, a
  crash report that quotes the request line. None of those see a header.

AlphaVantage had no choice — ``apikey`` in the query is its only mechanism, which is exactly
why :data:`~app.clients.base.SENSITIVE_PARAM_NAMES` exists. NewsAPI does have a choice, so it
takes the better one. ``tests/integration/test_client_newsapi.py`` asserts the key appears in
no log line and in no exception either way.

The trap: an error that arrives as ``200 OK``
---------------------------------------------

NewsAPI answers a refusal with ``{"status": "error", "code": …, "message": …}``. It sends
that body with a 4xx *sometimes* — and with a ``200`` the rest of the time. To
:class:`~app.clients.base.BaseHTTPClient` a 2xx carrying valid JSON is a good response, so
the base cannot see the second case and :meth:`NewsApiClient._parse_feed` has to.

When the refusal *does* arrive as a 4xx the base classifies it from the status line and this
module never sees the body at all. That is deliberate and the two paths agree: a 429 and a
``200`` + ``"code": "rateLimited"`` both leave here as
``details["reason"] == "rate_limited"`` with the same message, because both raise through
:meth:`~app.clients.base.BaseHTTPClient._error`. They differ only in the keys that genuinely
do not exist on the body-detected path — ``attempts`` and ``status_code`` — which is ANV-18's
rule and the reason a fabricated ``1`` would be worse than an absent key.

Why there is still no ``_check_payload`` hook on the base
----------------------------------------------------------

ANV-18 declined to generalise its 200-that-means-failure check on the grounds that a hook
with one caller has its shape fixed by a single example, and left the decision to this
ticket as the second caller. Having written the second one: **the genuinely common part was
already lifted, and what is left is not common.**

* The shared part is :meth:`~app.clients.base.BaseHTTPClient._error` — the message
  templates, the ``details`` keys, the 502 contract and the ``attempts=None`` case ANV-18
  added. Both vendors use it unchanged, which is why a consumer cannot tell a body-detected
  rate limit from a transport-detected one. That is the whole value a hook would have
  delivered, and it is already delivered.
* What differs is not merely *the predicate*, it is the **kind** of predicate. AlphaVantage
  signals failure by the *presence of a top-level key* (``"Note"``, ``"Error Message"``) and
  has to check at the top level only, because a healthy payload carries ``"1. Information"``
  nested inside ``Meta Data``. NewsAPI signals it by the *value of a required field*
  (``status``) and then needs a second lookup (``code``) to decide **which** failure it is —
  a mapping AlphaVantage has no analogue for. A ``payload -> Failure | None`` hook expresses
  both only by being empty enough to express anything.
* A hook would also have to answer a question neither vendor wants answered the same way:
  does a body-detected failure re-enter the retry loop? Placing the check in
  ``request_json`` implies yes, which would silently overturn ANV-18's asserted "the call is
  not repeated". Placing it after the loop makes it a second traversal of a payload the
  subclass's parser is about to walk anyway, to save one ``raise`` statement.

So the two stay separate, each check written where it is read: in the parser, beside the
knowledge of what the payload means. If a *third* vendor arrives whose body-level failure is
shaped like one of these two, that is the pair to generalise from — not this one.

No key configured
-----------------

``NEWSAPI_API_KEY`` is blank in ``.env.example`` and blank on any fresh clone, so the
un-configured case is the *default* case, not an edge one. This client refuses **before**
building a request: see :meth:`NewsApiClient._require_key`. Sending a keyless call would
spend a round trip to be told ``apiKeyMissing``, and would surface to the operator as
``reason: "client_error"`` — indistinguishable from a malformed query. Instead the failure
says what it is (``reason: "not_configured"``) and what to do about it
(``setting: "NEWSAPI_API_KEY"``), in the response body, with no log-reading required.

It is deliberately **not** raised through
:meth:`~app.clients.base.BaseHTTPClient._error`: that method maps a
:class:`~app.clients.base.Failure`, and every member of that enum describes how a *call*
went wrong. No call was made here. It is still
:class:`~app.domain.errors.ExternalServiceError` — ``CLAUDE.md`` §3 makes that the layer's
one exit — so it is still a 502, which is the honest status: Anvex is up, and the upstream
is unusable from here.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from typing import Any, ClassVar, Final

from pydantic import BaseModel, ConfigDict, SecretStr

from app.clients.base import BaseHTTPClient, Failure
from app.domain.errors import ExternalServiceError
from app.settings import Settings

# ---------------------------------------------------------------------------------------
# The vendor's wire vocabulary
# ---------------------------------------------------------------------------------------

#: The front page. Filtered by country/category, ordered by the vendor's own editorial rank.
TOP_HEADLINES_PATH: Final[str] = "/v2/top-headlines"

#: Full-text search over everything NewsAPI indexes. The endpoint a symbol query needs.
EVERYTHING_PATH: Final[str] = "/v2/everything"

#: The header the credential travels in. See the module docstring for why not ``apiKey=``.
API_KEY_HEADER: Final[str] = "X-Api-Key"

#: The vendor's own ceiling on ``pageSize``. Asking for more is a ``parameterInvalid``.
MAX_PAGE_SIZE: Final[int] = 100

#: What the old router asked for, and still the right default for a US-market research app.
DEFAULT_COUNTRY: Final[str] = "us"

#: ``/v2/everything`` orders by relevancy unless told otherwise. Anvex ranks the result set
#: itself (``app/domain/news.py``), so it asks for the ordering with the least vendor
#: opinion baked into it and re-ranks from there.
DEFAULT_SORT: Final[str] = "publishedAt"

#: Top-level envelope keys.
STATUS_KEY: Final[str] = "status"
CODE_KEY: Final[str] = "code"
TOTAL_RESULTS_KEY: Final[str] = "totalResults"
ARTICLES_KEY: Final[str] = "articles"

#: ``status`` is ``"ok"`` or ``"error"`` and nothing else.
STATUS_OK: Final[str] = "ok"
STATUS_ERROR: Final[str] = "error"

#: The vendor's per-article field names, mapped onto this module's model fields. Only the
#: two camel-cased ones need saying; the rest already match.
URL_TO_IMAGE_KEY: Final[str] = "urlToImage"
PUBLISHED_AT_KEY: Final[str] = "publishedAt"

#: NewsAPI's documented ``code`` values, mapped onto the shared failure taxonomy. The
#: mapping is the interesting part of this module, and two entries are judgement calls:
#:
#: * ``apiKeyExhausted`` is a **rate limit**, not a rejection. The quota resets; waiting is
#:   the fix, so it gets the reason ANV-22 reschedules on.
#: * ``maximumResultsReached`` is **not**, despite reading like one. It means the requested
#:   page lies past the plan's hard result ceiling, and that is true forever — the same page
#:   will be refused at any hour of any day. Filing it as ``rate_limited`` would hand ANV-22
#:   a reschedule signal for work that can never succeed, which is a loop, not a retry.
#:
#: ``unexpectedError`` is the vendor admitting fault, so it is a server error and therefore
#: retryable. Anything unrecognised falls to ``CLIENT_ERROR``: a refusal we cannot classify
#: is still a refusal, and treating an unknown code as retryable would triple the cost of
#: every future error NewsAPI invents.
ERROR_CODE_FAILURES: Final[Mapping[str, Failure]] = {
    "apiKeyDisabled": Failure.CLIENT_ERROR,
    "apiKeyExhausted": Failure.RATE_LIMITED,
    "apiKeyInvalid": Failure.CLIENT_ERROR,
    "apiKeyMissing": Failure.CLIENT_ERROR,
    "maximumResultsReached": Failure.CLIENT_ERROR,
    "parameterInvalid": Failure.CLIENT_ERROR,
    "parametersMissing": Failure.CLIENT_ERROR,
    "rateLimited": Failure.RATE_LIMITED,
    "sourceDoesNotExist": Failure.CLIENT_ERROR,
    "sourcesTooMany": Failure.CLIENT_ERROR,
    "unexpectedError": Failure.SERVER_ERROR,
}

#: Where an unrecognised ``code`` lands. See above.
UNKNOWN_CODE_FAILURE: Final[Failure] = Failure.CLIENT_ERROR

#: ``reason`` for the one failure that is Anvex's fault rather than the vendor's.
NOT_CONFIGURED: Final[str] = "not_configured"

#: The settings field an operator has to fill in. Named in ``details`` so the 502 is
#: actionable from the response body alone.
API_KEY_SETTING: Final[str] = "NEWSAPI_API_KEY"


# ---------------------------------------------------------------------------------------
# The typed result
# ---------------------------------------------------------------------------------------


class NewsSource(BaseModel):
    """Who published the article. Both halves are genuinely nullable upstream.

    ``id`` is only populated for the ~130 outlets NewsAPI has a slug for; everything else
    it indexes carries a ``name`` and a null ``id``. The old router's own captured payloads
    show both shapes side by side.
    """

    model_config = ConfigDict(frozen=True)

    id: str | None = None
    name: str | None = None


class NewsArticle(BaseModel):
    """One article, in the vendor's own words.

    Deliberately **not** an ``app.schemas`` model: that is Anvex's public shape and a vendor
    does not share it (the AST sweep enforces this). ``app/schemas/news.py`` is the
    projection, and ``app/services/news.py`` is where the two meet.

    **Almost everything is optional, and that is a report rather than a defence.** NewsAPI
    really does return ``null`` for ``author``, ``description``, ``urlToImage`` and
    ``content`` — the majority of the old router's pasted payload has three of the four
    null — and it emits withdrawn articles with the literal title ``"[Removed]"``. Deciding
    that an article with no title is unusable is an *Anvex* rule, so it lives in
    ``app/domain/news.py`` and this model reports what was said.

    The one field this module is strict about is :attr:`published_at`: absent or ``null`` is
    ``None``, but a **present-but-unparseable** timestamp is a malformed response. That line
    is ``CLAUDE.md`` §3's "a parser never silently repairs" — absence is a fact, a broken
    ISO-8601 string is a break.
    """

    model_config = ConfigDict(frozen=True)

    source: NewsSource = NewsSource()
    author: str | None = None
    title: str | None = None
    description: str | None = None
    url: str | None = None
    url_to_image: str | None = None
    #: Timezone-aware. A naive timestamp is refused rather than assumed to be UTC.
    published_at: dt.datetime | None = None
    content: str | None = None

    @property
    def source_name(self) -> str | None:
        """The outlet's display name, flattened off :attr:`source`.

        Present because ``app/domain/news.py`` ranks and de-duplicates on the outlet, and a
        pure rule should not have to reach through a nested vendor object to find it.
        """
        return self.source.name


class NewsFeed(BaseModel):
    """A whole ``top-headlines`` or ``everything`` response.

    :attr:`total_results` is the vendor's count of everything matching the query, not the
    length of :attr:`articles` — the articles are one page of it. It is carried because
    dropping it would leave a caller unable to tell "that is all there is" from "that is all
    you asked for", and reported unchanged: it counts what NewsAPI matched, *before* Anvex
    de-duplicates, and ``app/services/news.py`` documents why the two numbers differ.
    """

    model_config = ConfigDict(frozen=True)

    total_results: int
    #: In the vendor's order. Reordering is a transformation and this layer reports.
    articles: tuple[NewsArticle, ...]


# ---------------------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------------------


class NewsApiClient(BaseHTTPClient):
    """NewsAPI's HTTP surface. Two class attributes, one credential, two operations.

    Usage::

        async with NewsApiClient(settings) as vendor:
            feed = await vendor.fetch_top_headlines(page_size=50)
    """

    vendor: ClassVar[str] = "newsapi"
    base_url: ClassVar[str] = "https://newsapi.org"

    def __init__(self, settings: Settings, **kwargs: Any) -> None:
        # Stays a `SecretStr` for the client's whole life. The base unwraps it while
        # building one request and never stores the plaintext — see `CLAUDE.md` §3.
        self._api_key = settings.newsapi_api_key
        super().__init__(**kwargs)

    def auth_headers(self) -> Mapping[str, SecretStr | str]:
        """The credential, as a header. See the module docstring for why not a query param.

        The base registers the value as one of the call's secrets either way, so
        :func:`~app.clients.base.scrub` still covers anything a library says about it.
        """
        return {API_KEY_HEADER: self._api_key}

    @property
    def is_configured(self) -> bool:
        """Whether a key has been supplied at all.

        Public because a *caller* may reasonably want to answer "is this feature available
        here" without provoking a failure — but note that reading it is not a substitute for
        handling the error, since a key can be present and still be rejected.
        """
        return bool(self._api_key.get_secret_value().strip())

    # ----- operations --------------------------------------------------------------

    async def fetch_top_headlines(
        self,
        *,
        country: str = DEFAULT_COUNTRY,
        category: str | None = None,
        page_size: int = MAX_PAGE_SIZE,
        page: int = 1,
    ) -> NewsFeed:
        """The front page.

        :param country: two-letter ISO code. NewsAPI requires ``country``, ``category``,
            ``sources`` or ``q`` — at least one — and a country is the only one of those
            that is meaningful with no further input from the caller.
        :param category: ``business``, ``technology``, … Optional, and *not* defaulted to
            ``business`` here: which slice of the news an investment app should show is an
            Anvex product decision, not a fact about NewsAPI.
        :param page_size: articles per page, up to :data:`MAX_PAGE_SIZE`. Passed through
            unclamped — the vendor owns its own ceiling and says so with a
            ``parameterInvalid``, and silently shrinking a caller's request would hide that.
        :raises ExternalServiceError: for every failure, including the ones that arrive as a
            perfectly valid ``200`` and the one where no key is configured.
        """
        self._require_key()
        params: dict[str, Any] = {"country": country, "pageSize": page_size, "page": page}
        if category is not None:
            params["category"] = category
        return self._parse_feed(await self.get_json(TOP_HEADLINES_PATH, params=params))

    async def fetch_everything(
        self,
        query: str,
        *,
        language: str | None = "en",
        sort_by: str = DEFAULT_SORT,
        page_size: int = MAX_PAGE_SIZE,
        page: int = 1,
    ) -> NewsFeed:
        """Full-text search.

        :param query: the vendor's ``q``, taken as a primitive and sent verbatim. Building
            one out of a ticker and a company name is an Anvex rule and lives in
            ``app/domain/news.py`` — a client that composed the query would be deciding what
            Anvex means by "news about this security".
        :param language: two-letter ISO code, or ``None`` for every language.
        :raises ExternalServiceError: as above.
        """
        self._require_key()
        params: dict[str, Any] = {
            "q": query,
            "sortBy": sort_by,
            "pageSize": page_size,
            "page": page,
        }
        if language is not None:
            params["language"] = language
        return self._parse_feed(await self.get_json(EVERYTHING_PATH, params=params))

    # ----- parsing -----------------------------------------------------------------
    #
    # The `try`s below are not the `try` a subclass is forbidden (`CLAUDE.md` §3): that one
    # wraps the *request*, and the base owns it. These wrap `fromisoformat`, which has no
    # other spelling.

    def _require_key(self) -> None:
        """Refuse before spending a round trip, when there is no key to spend it with.

        See the module docstring: this is the *default* state of a fresh clone, so the
        failure has to name itself precisely rather than arriving disguised as a rejected
        request.
        """
        if not self.is_configured:
            raise ExternalServiceError(
                self.vendor,
                f"The upstream service '{self.vendor}' is not configured.",
                details={"reason": NOT_CONFIGURED, "setting": API_KEY_SETTING},
            )

    def _parse_feed(self, payload: Any) -> NewsFeed:
        """Validate a decoded body into :class:`NewsFeed`, or raise.

        Order matters: ``status`` is checked before anything is read out of the envelope,
        because an error body has no ``articles`` at all and "missing articles" would be a
        far worse diagnosis than "your key is invalid".
        """
        if not isinstance(payload, Mapping):
            raise self._error(Failure.MALFORMED)

        status = payload.get(STATUS_KEY)
        if status == STATUS_ERROR:
            raise self._error(self._failure_for(payload.get(CODE_KEY)))
        if status != STATUS_OK:
            # Neither "ok" nor "error": not a NewsAPI envelope at all.
            raise self._error(Failure.MALFORMED)

        articles = payload.get(ARTICLES_KEY)
        if not isinstance(articles, list):
            raise self._error(Failure.MALFORMED)

        return NewsFeed(
            total_results=self._count(payload.get(TOTAL_RESULTS_KEY), len(articles)),
            # An empty page is a legitimate answer — a query nobody has written about is a
            # result, not a failure — so it parses to `()` and the service decides.
            articles=tuple(self._parse_article(row) for row in articles),
        )

    @staticmethod
    def _failure_for(code: Any) -> Failure:
        """Map the vendor's ``code`` onto the shared taxonomy. See :data:`ERROR_CODE_FAILURES`."""
        if not isinstance(code, str):
            return UNKNOWN_CODE_FAILURE
        return ERROR_CODE_FAILURES.get(code, UNKNOWN_CODE_FAILURE)

    def _count(self, raw: Any, fallback: int) -> int:
        """``totalResults``, or the number of articles actually present.

        Falling back rather than failing: the count is a convenience for paging, and an
        article list that arrived intact should not be discarded because the header count
        beside it was not an integer. A *wrong type* is tolerated; a wrong article is not.
        """
        if isinstance(raw, bool) or not isinstance(raw, int):
            return fallback
        return max(raw, 0)

    def _parse_article(self, row: Any) -> NewsArticle:
        """One element of ``articles``, typed."""
        if not isinstance(row, Mapping):
            raise self._error(Failure.MALFORMED)

        source = row.get("source")
        source = source if isinstance(source, Mapping) else {}

        return NewsArticle(
            source=NewsSource(
                id=self._optional_text(source.get("id")),
                name=self._optional_text(source.get("name")),
            ),
            author=self._optional_text(row.get("author")),
            title=self._optional_text(row.get("title")),
            description=self._optional_text(row.get("description")),
            url=self._optional_text(row.get("url")),
            url_to_image=self._optional_text(row.get(URL_TO_IMAGE_KEY)),
            published_at=self._timestamp(row.get(PUBLISHED_AT_KEY)),
            content=self._optional_text(row.get("content")),
        )

    def _timestamp(self, raw: Any) -> dt.datetime | None:
        """``"2023-12-14T22:09:00Z"`` as an aware datetime, or ``None`` when absent.

        A naive timestamp is refused rather than assumed to be UTC. Assuming would be the
        silent repair ``CLAUDE.md`` §3 forbids, and it would be assuming about the one field
        ``app/domain/news.py`` ranks on — an article an hour out of place is a wrong answer
        that looks exactly like a right one.
        """
        if raw is None:
            return None
        if not isinstance(raw, str):
            raise self._error(Failure.MALFORMED)
        text = raw.strip()
        if not text:
            return None
        try:
            moment = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as error:
            raise self._error(Failure.MALFORMED) from error
        if moment.tzinfo is None:
            raise self._error(Failure.MALFORMED)
        return moment

    @staticmethod
    def _optional_text(raw: Any) -> str | None:
        """A vendor string, or ``None`` when it is absent, null, blank or not a string.

        Nullability here is the vendor's normal behaviour rather than an error, so a
        non-string is flattened to ``None`` instead of failing the whole feed. Whether an
        article missing a title is *usable* is ``app/domain/news.py``'s question.
        """
        if not isinstance(raw, str):
            return None
        text = raw.strip()
        return text or None


__all__ = [
    "API_KEY_HEADER",
    "API_KEY_SETTING",
    "ARTICLES_KEY",
    "CODE_KEY",
    "DEFAULT_COUNTRY",
    "DEFAULT_SORT",
    "ERROR_CODE_FAILURES",
    "EVERYTHING_PATH",
    "MAX_PAGE_SIZE",
    "NOT_CONFIGURED",
    "PUBLISHED_AT_KEY",
    "STATUS_ERROR",
    "STATUS_KEY",
    "STATUS_OK",
    "TOP_HEADLINES_PATH",
    "TOTAL_RESULTS_KEY",
    "UNKNOWN_CODE_FAILURE",
    "URL_TO_IMAGE_KEY",
    "NewsApiClient",
    "NewsArticle",
    "NewsFeed",
    "NewsSource",
]
