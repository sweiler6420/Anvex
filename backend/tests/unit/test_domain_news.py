"""The pure news rules: de-duplication, ranking, and the query a symbol turns into.

``tests/unit/`` (``CLAUDE.md`` §6): no fixtures, no I/O, no database, no clock. Every case
below is arithmetic and string handling on plain values, so this module runs at full speed
with Docker stopped and is where the edge cases belong — not behind an HTTP mock and
certainly not behind a route.

The articles here are a **three-line stub**, not the vendor model. That is the point of
``app.domain.news.Article`` being a :class:`typing.Protocol`: ``app/domain/`` sits below
``app/clients/`` in the dependency order and may not import it, so a rule that ranks articles
has to be expressed structurally. If this file could only be written by importing
``app.clients.newsapi``, the layering would be wrong.

De-duplication is the substance of this module and it is tested against the shapes NewsAPI
genuinely produces: one wire story carried by three outlets with the outlet's name glued onto
each headline, the same link decorated with different tracking parameters, and the literal
``"[Removed]"`` tombstone the vendor leaves behind when an article is withdrawn.
"""

from __future__ import annotations

import ast
import datetime as dt
import random
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from app.domain import news as domain_news
from app.domain.news import (
    COMPLETENESS_FIELDS,
    COMPLETENESS_WEIGHT,
    MAX_OUTLET_WORDS,
    PLACEHOLDER_TITLES,
    RECENCY_HORIZON_HOURS,
    RECENCY_WEIGHT,
    completeness_score,
    dedupe_and_rank,
    is_placeholder,
    is_usable,
    normalise_title,
    normalise_url,
    recency_score,
    score,
    search_terms,
    strip_outlet,
    title_key,
    url_key,
)

NOW = dt.datetime(2026, 3, 2, 12, 0, tzinfo=dt.UTC)


def source_tree() -> ast.Module:
    return ast.parse(Path(domain_news.__file__).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Stub:
    """The smallest thing that satisfies :class:`~app.domain.news.Article`.

    Deliberately not the vendor's model: if these rules needed one, they would be in the
    wrong layer.
    """

    url: str | None = "https://example.com/a"
    title: str | None = "A headline"
    source_name: str | None = "Reuters"
    description: str | None = None
    content: str | None = None
    url_to_image: str | None = None
    published_at: dt.datetime | None = NOW


def hours_ago(hours: float) -> dt.datetime:
    return NOW - dt.timedelta(hours=hours)


# ---------------------------------------------------------------------------------------
# purity
# ---------------------------------------------------------------------------------------


class TestPurity:
    """``app/domain/`` is pure by rule, and a convention that lives only in prose gets
    broken — so it is parsed out of the source, as ``test_domain_stock_data.py`` does."""

    def test_it_imports_no_framework_no_orm_and_no_settings(self) -> None:
        modules: set[str] = set()
        for node in ast.walk(source_tree()):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)
        roots = {module.split(".")[0] for module in modules}

        assert "fastapi" not in roots
        assert "starlette" not in roots
        assert "sqlalchemy" not in roots
        assert "httpx" not in roots
        assert "app.settings" not in modules
        # Nothing from `app` at all: these rules need no error vocabulary and — the point of
        # the Protocol — no vendor model either. Importing `app.clients` here would invert
        # `CLAUDE.md` §3's dependency order.
        assert {module for module in modules if module.startswith("app")} == set()

    def test_it_never_reads_a_clock(self) -> None:
        """``now`` is injected. That is what makes every recency case below exact."""
        clock_calls = {"now", "utcnow", "today", "monotonic", "perf_counter", "time_ns"}
        offenders = [
            name
            for node in ast.walk(source_tree())
            if isinstance(node, ast.Call)
            for name in [
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", None)
            ]
            if name in clock_calls
        ]

        assert offenders == [], f"domain/news.py must take a clock as a parameter: {offenders}"

    def test_it_writes_no_sql_and_reads_no_environment(self) -> None:
        source = Path(domain_news.__file__).read_text(encoding="utf-8")
        assert "select(" not in source
        assert "get_settings" not in source
        assert "os.environ" not in source


# ---------------------------------------------------------------------------------------
# URL identity
# ---------------------------------------------------------------------------------------


class TestNormaliseUrl:
    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_nothing_is_not_an_identity(self, value: str | None) -> None:
        assert normalise_url(value) is None

    def test_the_host_is_folded_and_the_path_is_not(self) -> None:
        """Hosts are case-insensitive; plenty of servers' paths are not, and folding one
        would merge two genuinely different articles."""
        assert normalise_url("HTTPS://WWW.Reuters.COM/World/Article") == (
            "https://reuters.com/World/Article"
        )

    def test_a_www_prefix_is_not_a_different_site(self) -> None:
        assert normalise_url("https://www.reuters.com/a") == normalise_url("https://reuters.com/a")

    def test_a_trailing_slash_is_not_a_different_page(self) -> None:
        assert normalise_url("https://reuters.com/a/") == normalise_url("https://reuters.com/a")

    def test_a_fragment_is_not_a_different_page(self) -> None:
        assert normalise_url("https://reuters.com/a#top") == normalise_url("https://reuters.com/a")

    @pytest.mark.parametrize(
        "decorated",
        [
            "https://reuters.com/a?utm_source=twitter",
            "https://reuters.com/a?utm_source=x&utm_medium=social&utm_campaign=q1",
            "https://reuters.com/a?fbclid=abc123",
            "https://reuters.com/a?ref=newsletter",
            "https://reuters.com/a?UTM_Source=Twitter",
        ],
    )
    def test_tracking_parameters_are_not_part_of_the_page(self, decorated: str) -> None:
        """The same link shared through three channels is one page, and NewsAPI indexes all
        three decorations."""
        assert normalise_url(decorated) == normalise_url("https://reuters.com/a")

    def test_a_meaningful_parameter_survives(self) -> None:
        """``?id=42`` may be the whole article. Stripping every parameter would merge a
        site's entire archive into one row."""
        assert normalise_url("https://site.com/read?id=42") != normalise_url(
            "https://site.com/read?id=43"
        )

    def test_parameter_order_is_not_part_of_the_page(self) -> None:
        assert normalise_url("https://s.com/a?b=2&a=1") == normalise_url("https://s.com/a?a=1&b=2")

    def test_a_string_with_no_host_is_still_an_identity(self) -> None:
        """A worse one, but discarding it would make two unrelated oddities collide."""
        assert normalise_url("  Not/A/Url  ") == "not/a/url"

    def test_two_different_articles_stay_different(self) -> None:
        assert normalise_url("https://reuters.com/a") != normalise_url("https://reuters.com/b")


# ---------------------------------------------------------------------------------------
# title identity
# ---------------------------------------------------------------------------------------


class TestStripOutlet:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Fed holds rates steady - Reuters", "Fed holds rates steady"),
            ("Fed holds rates steady | CNBC", "Fed holds rates steady"),
            ("Fed holds rates steady — WIRED", "Fed holds rates steady"),
            ("Fed holds rates - The Wall Street Journal", "Fed holds rates"),
            ("Congress passes the bill - Yahoo Finance", "Congress passes the bill"),
        ],
    )
    def test_a_trailing_masthead_is_removed(self, title: str, expected: str) -> None:
        assert strip_outlet(title) == expected

    def test_only_the_last_separator_is_considered(self) -> None:
        assert strip_outlet("A - B - Reuters") == "A - B"

    def test_a_headline_clause_is_not_a_masthead(self) -> None:
        """The failure mode that matters: over-stripping merges two different stories, while
        under-stripping merely fails to merge two copies of one."""
        assert strip_outlet("Markets today - stocks rise") == "Markets today - stocks rise"

    def test_a_long_capitalised_tail_is_not_a_masthead_either(self) -> None:
        tail = " ".join(["Word"] * (MAX_OUTLET_WORDS + 1))
        assert strip_outlet(f"Headline - {tail}") == f"Headline - {tail}"

    def test_the_articles_own_outlet_is_removed_even_when_it_looks_like_a_clause(self) -> None:
        """Certain knowledge beats the heuristic, so it is tried first."""
        assert strip_outlet("Markets today - stocks rise", "stocks rise") == "Markets today"

    def test_a_named_outlet_is_matched_case_insensitively(self) -> None:
        assert strip_outlet("Fed holds rates - reuters", "Reuters") == "Fed holds rates"

    def test_a_title_that_is_only_an_outlet_is_left_alone(self) -> None:
        """Stripping would leave nothing, and nothing is not a better identity."""
        assert strip_outlet("Reuters", "Reuters") == "Reuters"

    def test_a_different_outlet_falls_back_to_the_heuristic(self) -> None:
        """Syndicated copy often names the *originating* outlet, not the indexing one."""
        assert strip_outlet("Fed holds rates - Reuters", "Yahoo Finance") == "Fed holds rates"


class TestNormaliseTitle:
    @pytest.mark.parametrize("value", [None, "", "   ", "!!!", "- - -"])
    def test_a_title_that_says_nothing_is_no_identity(self, value: str | None) -> None:
        assert normalise_title(value) is None

    def test_punctuation_and_case_are_not_part_of_the_story(self) -> None:
        assert normalise_title("Fed Holds Rates Steady!") == normalise_title(
            "fed holds rates steady"
        )

    def test_a_typographic_apostrophe_matches_an_ascii_one(self) -> None:
        """The single commonest difference between two outlets' copy of one headline."""
        # Written as an escape so this file's own source stays ASCII; it is the character
        # every publishing CMS substitutes for a typed apostrophe.
        curly = "Apple\u2019s Q3 beat"
        assert normalise_title(curly) == normalise_title("Apple's Q3 beat")

    def test_two_outlets_running_the_same_wire_copy_share_an_identity(self) -> None:
        assert normalise_title("Congress passes the bill - Reuters", "Reuters") == normalise_title(
            "Congress passes the bill | CNBC", "CNBC"
        )

    def test_different_stories_keep_different_identities(self) -> None:
        assert normalise_title("Fed holds rates") != normalise_title("Fed cuts rates")


class TestUsability:
    def test_a_complete_article_is_usable(self) -> None:
        assert is_usable(Stub()) is True

    @pytest.mark.parametrize("missing", ["url", "title", "published_at"])
    def test_the_three_fields_a_feed_cannot_do_without(self, missing: str) -> None:
        """A headline to render, a link to open, a timestamp to order by. This is also what
        lets ``app/schemas/news.py`` declare those three required."""
        assert is_usable(replace(Stub(), **{missing: None})) is False  # type: ignore[arg-type]

    @pytest.mark.parametrize("blank", ["", "   ", "!!!"])
    def test_a_title_that_reduces_to_nothing_is_not_a_title(self, blank: str) -> None:
        assert is_usable(replace(Stub(), title=blank)) is False

    def test_the_vendors_tombstone_is_not_an_article(self) -> None:
        """NewsAPI does not remove a withdrawn item, it overwrites its fields."""
        removed = Stub(title="[Removed]", url="https://removed.com", source_name="[Removed]")

        assert is_placeholder(removed) is True
        assert is_usable(removed) is False

    def test_the_placeholder_set_is_not_empty(self) -> None:
        """Guards the guard: an empty set would make the check vacuous."""
        assert PLACEHOLDER_TITLES

    def test_a_missing_optional_field_does_not_make_an_article_unusable(self) -> None:
        assert is_usable(Stub(description=None, content=None, url_to_image=None)) is True


# ---------------------------------------------------------------------------------------
# ranking
# ---------------------------------------------------------------------------------------


class TestRecencyScore:
    def test_this_second_is_the_top_of_the_scale(self) -> None:
        assert recency_score(Stub(published_at=NOW), now=NOW) == 1.0

    def test_it_falls_linearly_to_the_horizon(self) -> None:
        half = RECENCY_HORIZON_HOURS / 2
        assert recency_score(Stub(published_at=hours_ago(half)), now=NOW) == pytest.approx(0.5)

    def test_the_horizon_is_the_floor(self) -> None:
        assert recency_score(Stub(published_at=hours_ago(RECENCY_HORIZON_HOURS)), now=NOW) == 0.0

    def test_nothing_older_than_the_horizon_scores_below_zero(self) -> None:
        assert recency_score(Stub(published_at=hours_ago(10_000)), now=NOW) == 0.0

    def test_a_future_timestamp_does_not_outrank_the_present(self) -> None:
        """Vendors' clocks skew and outlets post-date embargoed copy. Neither is a reason to
        beat a story that has actually happened."""
        assert recency_score(Stub(published_at=hours_ago(-72)), now=NOW) == 1.0

    def test_an_undated_article_scores_nothing(self) -> None:
        assert recency_score(Stub(published_at=None), now=NOW) == 0.0

    def test_a_naive_published_timestamp_is_refused(self) -> None:
        naive = dt.datetime(2026, 3, 2, 12, 0)

        with pytest.raises(ValueError, match="timezone-aware"):
            recency_score(Stub(published_at=naive), now=NOW)


class TestCompletenessScore:
    def test_nothing_optional_scores_nothing(self) -> None:
        assert completeness_score(Stub()) == 0.0

    def test_everything_optional_scores_one(self) -> None:
        full = Stub(description="d", content="c", url_to_image="https://i/x.png")
        assert completeness_score(full) == 1.0

    def test_each_field_is_worth_the_same(self) -> None:
        expected = 1 / len(COMPLETENESS_FIELDS)
        for field in COMPLETENESS_FIELDS:
            article = replace(Stub(), **{field: "something"})  # type: ignore[arg-type]
            assert completeness_score(article) == pytest.approx(expected), field

    def test_a_blank_string_is_not_a_description(self) -> None:
        assert completeness_score(Stub(description="   ")) == 0.0


class TestScore:
    def test_the_weights_are_what_the_module_says_they_are(self) -> None:
        fresh_and_full = Stub(description="d", content="c", url_to_image="i")
        assert score(fresh_and_full, now=NOW) == pytest.approx(RECENCY_WEIGHT + COMPLETENESS_WEIGHT)

    def test_freshness_outweighs_completeness(self) -> None:
        """A news feed is for what happened, and a fully-illustrated week-old item is not it."""
        stale_but_rich = Stub(
            published_at=hours_ago(RECENCY_HORIZON_HOURS),
            description="d",
            content="c",
            url_to_image="i",
        )
        fresh_but_bare = Stub(published_at=NOW)

        assert score(fresh_but_bare, now=NOW) > score(stale_but_rich, now=NOW)

    def test_completeness_separates_two_equally_fresh_articles(self) -> None:
        bare = Stub(url="https://a.com/1", title="One")
        rich = Stub(url="https://a.com/2", title="Two", description="d")

        assert score(rich, now=NOW) > score(bare, now=NOW)


# ---------------------------------------------------------------------------------------
# de-duplication and ordering — the substance
# ---------------------------------------------------------------------------------------


class TestDedupeAndRank:
    def test_an_empty_feed_is_an_empty_result(self) -> None:
        assert dedupe_and_rank([], now=NOW) == ()

    def test_a_naive_now_is_refused(self) -> None:
        """A naive ``now`` would raise ``TypeError`` deep inside a sort, and resolving it in
        the server's local zone silently would be worse."""
        with pytest.raises(ValueError, match="timezone-aware"):
            dedupe_and_rank([Stub()], now=dt.datetime(2026, 3, 2, 12, 0))

    def test_unusable_articles_are_dropped(self) -> None:
        kept = dedupe_and_rank(
            [
                Stub(url="https://a.com/1", title="Real story"),
                Stub(url=None, title="No link"),
                Stub(url="https://a.com/2", title=None),
                Stub(url="https://a.com/3", title="Undated", published_at=None),
                Stub(url="https://removed.com", title="[Removed]"),
            ],
            now=NOW,
        )

        assert [a.title for a in kept] == ["Real story"]

    def test_the_same_url_twice_survives_once(self) -> None:
        kept = dedupe_and_rank(
            [Stub(title="One"), Stub(title="Two", url="https://example.com/a?utm_source=x")],
            now=NOW,
        )

        assert len(kept) == 1

    def test_the_same_story_from_two_outlets_survives_once(self) -> None:
        """Two different links, two different mastheads, one wire story."""
        reuters = Stub(
            url="https://reuters.com/congress-bill",
            title="Congress passes $886 billion defense policy bill - Reuters",
            source_name="Reuters",
        )
        syndicated = Stub(
            url="https://finance.yahoo.com/news/congress-bill-220900.html",
            title="Congress passes $886 billion defense policy bill | Yahoo Finance",
            source_name="Yahoo Finance",
        )

        assert len(dedupe_and_rank([reuters, syndicated], now=NOW)) == 1

    def test_the_survivor_of_a_duplicate_group_is_its_best_member(self) -> None:
        """Ranking runs before de-duplication for exactly this reason: keeping whichever copy
        the vendor happened to list first would throw away the one with the picture."""
        bare = Stub(url="https://a.com/x", title="One story - Reuters", source_name="Reuters")
        rich = Stub(
            url="https://b.com/y",
            title="One story | CNBC",
            source_name="CNBC",
            description="d",
            content="c",
            url_to_image="i",
        )

        kept = dedupe_and_rank([bare, rich], now=NOW)

        assert kept == (rich,)

    def test_three_copies_of_one_story_collapse_to_one(self) -> None:
        copies = [
            Stub(url=f"https://outlet{n}.com/story", title=f"One story - Outlet{n}")
            for n in range(3)
        ]

        assert len(dedupe_and_rank(copies, now=NOW)) == 1

    def test_different_stories_all_survive(self) -> None:
        distinct = [
            Stub(url=f"https://a.com/{n}", title=f"Story number {n}", published_at=hours_ago(n))
            for n in range(5)
        ]

        assert len(dedupe_and_rank(distinct, now=NOW)) == 5

    def test_the_freshest_story_comes_first(self) -> None:
        old = Stub(url="https://a.com/old", title="Old news", published_at=hours_ago(30))
        new = Stub(url="https://a.com/new", title="New news", published_at=hours_ago(1))

        assert dedupe_and_rank([old, new], now=NOW) == (new, old)

    def test_the_order_does_not_depend_on_the_order_the_vendor_answered_in(self) -> None:
        """The property that keeps a paged endpoint from repeating or skipping an article:
        the sort is total, so no pair is ever left tied and resolved by input position.
        """
        feed = [
            Stub(
                url=f"https://a.com/{n}",
                title=f"Story {n}",
                # Deliberately colliding scores: same age, same completeness.
                published_at=hours_ago(6),
            )
            for n in range(12)
        ]
        expected = dedupe_and_rank(feed, now=NOW)

        shuffler = random.Random(20260302)
        for _ in range(10):
            shuffled = feed[:]
            shuffler.shuffle(shuffled)
            assert dedupe_and_rank(shuffled, now=NOW) == expected

    def test_it_returns_what_it_was_given_not_a_copy(self) -> None:
        """The type parameter is bound to the Protocol, so the service gets vendor models
        back and has nothing to translate."""
        article = Stub()

        assert dedupe_and_rank([article], now=NOW)[0] is article

    def test_a_url_duplicate_is_caught_even_when_the_titles_differ(self) -> None:
        kept = dedupe_and_rank(
            [
                Stub(url="https://a.com/x", title="First wording of a story"),
                Stub(url="https://a.com/x?utm_campaign=q1", title="Second wording entirely"),
            ],
            now=NOW,
        )

        assert len(kept) == 1

    def test_missing_optional_fields_do_not_break_the_sort(self) -> None:
        """Everything optional is null on a large fraction of real wire copy."""
        feed = [
            Stub(url="https://a.com/1", title="One", description=None, url_to_image=None),
            Stub(url="https://a.com/2", title="Two", description="d", url_to_image=None),
            Stub(url="https://a.com/3", title="Three", description="d", url_to_image="i"),
        ]

        kept = dedupe_and_rank(feed, now=NOW)

        assert [a.title for a in kept] == ["Three", "Two", "One"]

    def test_the_result_is_a_tuple(self) -> None:
        """A ranked feed is a decision that has been made, not a list to keep appending to."""
        assert isinstance(dedupe_and_rank([Stub()], now=NOW), tuple)


# ---------------------------------------------------------------------------------------
# identity helpers used by the service and the tests above
# ---------------------------------------------------------------------------------------


class TestTheIdentityHelpers:
    def test_the_title_key_uses_the_articles_own_outlet(self) -> None:
        assert title_key(Stub(title="A story - Reuters", source_name="Reuters")) == "a story"

    def test_the_url_key_is_the_normalised_url(self) -> None:
        assert url_key(Stub(url="https://WWW.A.com/x/")) == "https://a.com/x"

    def test_both_are_none_when_the_field_is(self) -> None:
        assert title_key(Stub(title=None)) is None
        assert url_key(Stub(url=None)) is None


# ---------------------------------------------------------------------------------------
# the query a security turns into
# ---------------------------------------------------------------------------------------


class TestSearchTerms:
    def test_both_the_symbol_and_the_company_are_offered(self) -> None:
        """Neither works alone: ``q=CAT`` returns articles about cats, and the market
        coverage that only writes the symbol never says "Caterpillar"."""
        assert search_terms("CAT", "Caterpillar Inc.") == '"CAT" OR "Caterpillar Inc."'

    def test_each_term_is_quoted_as_a_phrase(self) -> None:
        assert search_terms("BRK.B", "Berkshire Hathaway Inc.").count('"') == 4

    def test_a_symbol_alone_is_a_valid_query(self) -> None:
        assert search_terms("AAPL") == '"AAPL"'

    @pytest.mark.parametrize("company", [None, "", "   "])
    def test_a_missing_company_name_is_not_an_empty_term(self, company: str | None) -> None:
        assert search_terms("AAPL", company) == '"AAPL"'

    def test_a_company_identical_to_its_ticker_is_not_repeated(self) -> None:
        assert search_terms("VISA", "visa") == '"VISA"'

    def test_a_quote_inside_a_name_cannot_break_the_query(self) -> None:
        """NewsAPI's query syntax has no escape for one, and an unbalanced quote would break
        the whole request rather than one term."""
        query = search_terms("XYZ", 'The "Best" Company')

        assert '"' * 2 not in query.replace('"XYZ"', "")
        assert query.count('"') == 4

    @pytest.mark.parametrize("ticker", ["", "   ", '"'])
    def test_a_blank_ticker_has_no_useful_query(self, ticker: str) -> None:
        """Refused here rather than sent, where it would be a ``parametersMissing`` one round
        trip later."""
        with pytest.raises(ValueError, match="ticker"):
            search_terms(ticker)
