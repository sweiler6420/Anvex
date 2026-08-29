"""Pure rules about a news feed: which articles are worth showing, and in what order.

``app/domain/`` is pure by rule (``CLAUDE.md`` §3) — plain data in, plain data out, no I/O
and **no clock read**. Every function here takes what it needs as an argument, including
``now``, and ``tests/unit/test_domain_news.py`` parses this module's source to keep that
true rather than trusting this paragraph.

Why de-duplication is real work here
------------------------------------

It would be easy to mistake this module for ceremony. It is not. A single wire story reaches
NewsAPI through every outlet that ran it, and NewsAPI indexes all of them:

* **The same story from two outlets.** ``"Congress passes $886 billion defense policy bill,
  Biden to sign into law - Reuters"`` is the exact headline the old router's pasted payload
  carried, and the syndication partners that ran the same Reuters copy carry the same
  sentence with a different tail. Note the tail: NewsAPI's ``title`` has the outlet's name
  appended after a ``-`` or a ``|`` far more often than not, which is precisely what defeats
  a naive equality check on the raw string.
* **The same URL twice.** Both endpoints can return it — ``/v2/everything`` because two of a
  caller's search terms matched the same article, and either endpoint because the same link
  arrives decorated with different tracking parameters (``?utm_source=…``), which makes two
  byte-different strings that are one page.
* **Withdrawn articles.** NewsAPI does not remove a retracted item, it replaces its fields
  with the literal ``"[Removed]"``. Left alone those pile up at the top of a recency sort.

So the rule is: normalise, rank, then keep the best representative of each story. Ranking
happens **before** de-duplication so the survivor of a duplicate group is the best member of
it rather than whichever one the vendor happened to list first.

The ordering is a total one
---------------------------

:func:`dedupe_and_rank` sorts on ``(-score, -published_at, url)`` — three keys, the last of
which is unique among survivors by construction, so no pair of articles is ever tied. That
matters more than it looks: a sort that falls back to input order would make the output of a
paged endpoint depend on the order the vendor happened to answer in, and page 2 could then
repeat or skip an article that page 1 already showed. Shuffling the input here cannot change
the output, and a unit test asserts exactly that.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from typing import Final, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# ---------------------------------------------------------------------------------------
# What this module needs an "article" to be
# ---------------------------------------------------------------------------------------


class Article(Protocol):
    """The shape these rules read. Structural, so nothing here imports a vendor model.

    ``app/clients/newsapi.py``'s :class:`~app.clients.newsapi.NewsArticle` satisfies it and
    so does a three-line test stub, which is the point: ``app/domain/`` sits *below*
    ``app/clients/`` in ``CLAUDE.md`` §3's dependency order and may not import upward. A
    frozen dataclass defined here instead would have forced the service to translate every
    article twice — once into the domain's spelling and once into the API's — to buy nothing.
    """

    @property
    def url(self) -> str | None: ...

    @property
    def title(self) -> str | None: ...

    @property
    def source_name(self) -> str | None: ...

    @property
    def description(self) -> str | None: ...

    @property
    def content(self) -> str | None: ...

    @property
    def url_to_image(self) -> str | None: ...

    @property
    def published_at(self) -> dt.datetime | None: ...


# ---------------------------------------------------------------------------------------
# Normalisation — the two identities a story can be recognised by
# ---------------------------------------------------------------------------------------

#: Query parameters that identify the *referrer*, never the article. Stripped before a URL
#: is used as an identity, so one page shared through three channels is one page.
TRACKING_PARAMS: Final[frozenset[str]] = frozenset(
    {
        "_hsenc",
        "_hsmi",
        "cmpid",
        "fbclid",
        "gclid",
        "icid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "msclkid",
        "ref",
        "src",
        "taid",
        "yptr",
    }
)

#: Any parameter starting with one of these is tracking too (``utm_source``, ``utm_medium``).
TRACKING_PREFIXES: Final[tuple[str, ...]] = ("utm_",)

#: Separators an outlet's name is appended after. NewsAPI titles use all four, and the last
#: two really are an em dash and an en dash — which is exactly why the ambiguous-character
#: rule is suppressed rather than obeyed here: a headline typed with an en dash is the case
#: this tuple exists to catch, and replacing it with a hyphen would delete the feature.
OUTLET_SEPARATORS: Final[tuple[str, ...]] = (" - ", " | ", " — ", " – ")  # noqa: RUF001

#: A trailing segment longer than this is a headline clause, not a byline. Four words covers
#: every long outlet name that exists (``The Wall Street Journal``, ``The New York Times``).
MAX_OUTLET_WORDS: Final[int] = 4

#: Lower-case words allowed *inside* an outlet name, which is otherwise required to be
#: capitalised throughout. Without the capitalisation test, ``"Markets today - stocks rise"``
#: would lose its second half and merge with an unrelated ``"Markets today"``; with it, only
#: a proper-noun tail is treated as a byline. Erring this way is the cheap direction: a
#: missed strip fails to merge two duplicates, a wrong strip merges two different stories.
OUTLET_CONNECTORS: Final[frozenset[str]] = frozenset({"and", "de", "of", "the"})

#: Normalised titles that identify a placeholder rather than an article. NewsAPI overwrites
#: a withdrawn item's fields with ``"[Removed]"`` rather than dropping the item.
PLACEHOLDER_TITLES: Final[frozenset[str]] = frozenset({"removed"})

_NON_ALPHANUMERIC = re.compile(r"[^0-9a-z]+")


def normalise_url(url: str | None) -> str | None:
    """A URL reduced to the page it names, or ``None`` if it names nothing.

    Case is folded on the scheme and host only — a path is case-sensitive on plenty of
    servers, and folding it would merge two genuinely different articles. ``www.`` goes, the
    fragment goes, tracking parameters go, a trailing slash goes, and what survives is
    sorted so ``?a=1&b=2`` and ``?b=2&a=1`` are one key.

    A string with no host is returned trimmed and lower-cased rather than discarded: it is
    still an identity, just a worse one.
    """
    if not url or not url.strip():
        return None

    parts = urlsplit(url.strip())
    if not parts.netloc:
        return url.strip().lower()

    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    query = urlencode(
        sorted(
            (name, value)
            for name, value in parse_qsl(parts.query, keep_blank_values=True)
            if not _is_tracking(name)
        )
    )
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


def _is_tracking(name: str) -> bool:
    lowered = name.lower()
    return lowered in TRACKING_PARAMS or lowered.startswith(TRACKING_PREFIXES)


def strip_outlet(title: str, outlet: str | None = None) -> str:
    """``"Fed holds rates steady - Reuters"`` → ``"Fed holds rates steady"``.

    Two tests, in order of confidence. If the article's own ``source.name`` is known and the
    title ends with it after a separator, that is certain and the tail goes. Otherwise the
    last separator's tail is removed only when it *looks* like a byline —
    :data:`MAX_OUTLET_WORDS` or fewer, every word capitalised bar a
    :data:`OUTLET_CONNECTORS` connector. The second test exists because a syndicated copy
    frequently names the **originating** outlet in the title rather than the indexing one,
    so ``source.name`` alone would leave half of a duplicate pair unstripped.
    """
    text = title.strip()
    if outlet and outlet.strip():
        stripped = _strip_named_outlet(text, outlet.strip())
        if stripped is not None:
            return stripped

    for separator in OUTLET_SEPARATORS:
        head, found, tail = text.rpartition(separator)
        if found and head.strip() and _looks_like_an_outlet(tail):
            return head.strip()
    return text


def _strip_named_outlet(title: str, outlet: str) -> str | None:
    """``title`` without a trailing ``<separator><outlet>``, or ``None`` if it has none."""
    for separator in OUTLET_SEPARATORS:
        suffix = f"{separator}{outlet}"
        if title.casefold().endswith(suffix.casefold()) and len(title) > len(suffix):
            return title[: -len(suffix)].strip()
    return None


def _looks_like_an_outlet(tail: str) -> bool:
    """Whether a title's trailing segment reads as a masthead rather than a clause."""
    words = tail.split()
    if not words or len(words) > MAX_OUTLET_WORDS:
        return False
    return all(word.lower() in OUTLET_CONNECTORS or word[:1].isupper() for word in words)


def normalise_title(title: str | None, outlet: str | None = None) -> str | None:
    """A headline reduced to the story it tells, or ``None`` if it tells none.

    The outlet's byline is removed, then everything that is not a digit or an ASCII letter
    becomes a space and the spaces collapse. That is what makes an ASCII apostrophe
    (``U+0027``) and a typographic one (``U+2019``) the same key — the single commonest
    difference between two outlets' copy of one headline, and one no equality check on the
    raw strings survives. Em dashes, curly quotes and the rest go the same way.
    """
    if not title or not title.strip():
        return None
    reduced = _NON_ALPHANUMERIC.sub(" ", strip_outlet(title, outlet).lower()).strip()
    return reduced or None


def title_key(article: Article) -> str | None:
    """The story identity of an article: its headline, minus whoever ran it."""
    return normalise_title(article.title, article.source_name)


def url_key(article: Article) -> str | None:
    """The page identity of an article, minus however it was linked to."""
    return normalise_url(article.url)


def is_placeholder(article: Article) -> bool:
    """Whether this is NewsAPI's tombstone for a withdrawn article rather than an article."""
    return title_key(article) in PLACEHOLDER_TITLES


def is_usable(article: Article) -> bool:
    """Whether an article can be shown at all.

    Three things a news feed cannot do without: a headline to render, a link to open, and a
    timestamp to order and date it by. An article missing any of them is dropped here rather
    than rendered as a blank row — the vendor genuinely returns those shapes, and none of
    them is an upstream failure.

    This is also what lets ``app/schemas/news.py`` declare those three fields **required**
    while the vendor model has all three optional: the guarantee is made here, once, by the
    rule every article passes through, rather than by a null check in every consumer.
    """
    return (
        title_key(article) is not None
        and url_key(article) is not None
        and article.published_at is not None
        and not is_placeholder(article)
    )


# ---------------------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------------------

#: How long an article takes to lose all of its recency score. Two days: long enough that a
#: Friday evening story still places on Sunday, short enough that last week never outranks
#: this morning.
RECENCY_HORIZON_HOURS: Final[float] = 48.0

#: Recency is three quarters of the score and completeness the other quarter. Freshness is
#: what a news feed is *for*; completeness only breaks ties between comparably fresh items,
#: by preferring the copy that will actually render with a summary and a picture.
RECENCY_WEIGHT: Final[float] = 0.75
COMPLETENESS_WEIGHT: Final[float] = 0.25

#: The optional fields whose presence makes a rendered card better. All three are routinely
#: ``null`` upstream, and which of two identical stories carries them is exactly the sort of
#: thing worth choosing between.
COMPLETENESS_FIELDS: Final[tuple[str, ...]] = ("description", "content", "url_to_image")

_SECONDS_PER_HOUR: Final[float] = 3600.0


def recency_score(article: Article, *, now: dt.datetime) -> float:
    """``1.0`` for something published this second, falling linearly to ``0.0`` at the
    horizon and staying there.

    An article stamped in the *future* scores ``1.0`` rather than more: vendors' clocks skew
    and some outlets post-date embargoed copy, and neither is a reason to outrank a story
    that has actually happened.
    """
    published = article.published_at
    if published is None:
        return 0.0
    _require_aware(published, "published_at")
    age_hours = (now - published).total_seconds() / _SECONDS_PER_HOUR
    if age_hours <= 0:
        return 1.0
    return max(0.0, 1.0 - age_hours / RECENCY_HORIZON_HOURS)


def completeness_score(article: Article) -> float:
    """The fraction of :data:`COMPLETENESS_FIELDS` this article actually carries."""
    present = sum(1 for field in COMPLETENESS_FIELDS if _has_text(getattr(article, field, None)))
    return present / len(COMPLETENESS_FIELDS)


def score(article: Article, *, now: dt.datetime) -> float:
    """The weighted rank of one article. Higher sorts first."""
    return RECENCY_WEIGHT * recency_score(article, now=now) + COMPLETENESS_WEIGHT * (
        completeness_score(article)
    )


def _sort_key(article: Article, *, now: dt.datetime) -> tuple[float, float, str]:
    """The total order. Three keys, and the third is unique among survivors.

    ``published_at`` breaks a score tie towards the fresher article — two items an hour apart
    inside the horizon can score identically once the weights round — and the normalised URL
    breaks everything else, so the result never depends on the order the vendor answered in.
    """
    published = article.published_at
    stamp = published.timestamp() if published is not None else float("-inf")
    return (-score(article, now=now), -stamp, url_key(article) or "")


# ---------------------------------------------------------------------------------------
# The rule the service calls
# ---------------------------------------------------------------------------------------


def dedupe_and_rank[ArticleT: Article](
    articles: Sequence[ArticleT], *, now: dt.datetime
) -> tuple[ArticleT, ...]:
    """Unusable articles dropped, the rest ranked, one survivor per story.

    The type parameter is bound to :class:`Article` so a caller gets back exactly the type
    it passed in — the service hands in vendor models and receives vendor models, never a
    lossy domain copy it would have to translate twice.

    :param articles: whatever the vendor returned, in whatever order.
    :param now: the reference time, **timezone-aware**. Injected rather than read, because
        ``app/domain/`` reads no clock (``CLAUDE.md`` §4) — which is also what makes every
        recency assertion below testable without a ``sleep``.
    :returns: the survivors, best first. Same element types that went in.
    :raises ValueError: ``now`` is naive. A naive datetime would compare against an aware
        ``published_at`` by raising ``TypeError`` deep inside a sort, and silently resolving
        it in the server's local zone would be worse.

    De-duplication runs **after** the sort and keeps the first article it sees under each
    identity, so the survivor of a group is its best member and not an accident of input
    order. Both identities are checked: an article whose URL is new but whose headline has
    already been seen is a syndication partner's copy and goes, and vice versa.
    """
    _require_aware(now, "now")

    ranked = sorted((a for a in articles if is_usable(a)), key=lambda a: _sort_key(a, now=now))

    seen_urls: set[str | None] = set()
    seen_titles: set[str | None] = set()
    kept: list[ArticleT] = []
    for article in ranked:
        url, title = url_key(article), title_key(article)
        if url in seen_urls or title in seen_titles:
            continue
        seen_urls.add(url)
        seen_titles.add(title)
        kept.append(article)
    return tuple(kept)


# ---------------------------------------------------------------------------------------
# Turning a security into a query
# ---------------------------------------------------------------------------------------

#: How NewsAPI's query syntax spells "either of these".
QUERY_DISJUNCTION: Final[str] = " OR "


def search_terms(ticker: str, company: str | None = None) -> str:
    """The ``q`` for "news about this security".

    ``search_terms("AAPL", "Apple Inc.")`` → ``'"AAPL" OR "Apple Inc."'``.

    Each term is quoted so NewsAPI treats it as a phrase, and both are offered because
    neither works alone: a bare ticker is a terrible query (``q=CAT`` returns articles about
    cats, ``q=ALL`` returns everything) and a bare company name misses the market coverage
    that only ever writes the symbol.

    This is an Anvex rule, not a NewsAPI fact — it encodes what *we* mean by news about a
    security — so it lives here and ``app/clients/newsapi.py`` takes the finished string as
    a primitive. Double quotes inside a name are dropped rather than escaped: NewsAPI's
    query syntax has no escape for them, and an unbalanced quote would break the whole
    query.

    :raises ValueError: ``ticker`` is blank once cleaned. There is no useful query for it,
        and an empty ``q`` is a ``parametersMissing`` from the vendor one round trip later.
    """
    symbol = _phrase(ticker)
    if not symbol:
        raise ValueError("a symbol query needs a ticker")
    name = _phrase(company or "")
    terms = [symbol] if not name or name.casefold() == symbol.casefold() else [symbol, name]
    return QUERY_DISJUNCTION.join(f'"{term}"' for term in terms)


def _phrase(term: str) -> str:
    """One search term, safe to sit inside double quotes."""
    return term.replace('"', " ").strip()


# ---------------------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------------------


def _has_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_aware(moment: dt.datetime, name: str) -> None:
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = [
    "COMPLETENESS_FIELDS",
    "COMPLETENESS_WEIGHT",
    "MAX_OUTLET_WORDS",
    "OUTLET_CONNECTORS",
    "OUTLET_SEPARATORS",
    "PLACEHOLDER_TITLES",
    "QUERY_DISJUNCTION",
    "RECENCY_HORIZON_HOURS",
    "RECENCY_WEIGHT",
    "TRACKING_PARAMS",
    "TRACKING_PREFIXES",
    "Article",
    "completeness_score",
    "dedupe_and_rank",
    "is_placeholder",
    "is_usable",
    "normalise_title",
    "normalise_url",
    "recency_score",
    "score",
    "search_terms",
    "strip_outlet",
    "title_key",
    "url_key",
]
