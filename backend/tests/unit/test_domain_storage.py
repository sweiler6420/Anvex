"""Unit tests for ``app/domain/storage.py``.

Pure tier: no fixtures, no bucket, no clock. Every key below is asserted **in full** rather
than against a regex, which is only possible because ``export_key`` takes both of its impure
inputs — the clock and the uniqueness token — as arguments.

The fixed timestamps are deliberately nowhere near the real one, so a test that passes could
not have passed by accidentally reading the wall clock.
"""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.domain import storage
from app.domain.storage import (
    CONTENT_TYPES,
    DEFAULT_CONTENT_TYPE,
    DEFAULT_DOWNLOAD_TTL,
    EXPORTS_PREFIX,
    MAX_DOWNLOAD_TTL,
    MAX_KEY_BYTES,
    MAX_SLUG_LENGTH,
    MIN_DOWNLOAD_TTL,
    content_type_for,
    export_key,
    export_prefix_for_day,
    export_prefix_for_owner,
    normalise_extension,
    owner_of_export_key,
    resolve_download_ttl,
    slugify,
    validate_key,
)

OWNER = uuid.UUID("11111111-2222-3333-4444-555555555555")
OTHER_OWNER = uuid.UUID("99999999-8888-7777-6666-555555555555")

#: Fixed, aware, and a long way from today.
NOW = datetime(2024, 3, 1, 9, 5, 7, tzinfo=UTC)
#: The same *instant* expressed in a non-UTC zone, for the partition tests. -10:00 puts it
#: on the previous calendar day, which is the whole point.
NOW_ELSEWHERE = NOW.astimezone(timezone(timedelta(hours=-10)))
NAIVE = datetime(2024, 3, 1, 9, 5, 7)

UNIQUE = "abcd1234"


def source_tree() -> ast.Module:
    return ast.parse(Path(storage.__file__).read_text(encoding="utf-8"))


class TestPurity:
    """``app/domain/`` is pure by rule, and a prose convention gets broken (``CLAUDE.md`` §3)."""

    def test_module_imports_nothing_from_a_layer_that_performs_io(self) -> None:
        modules: set[str] = set()
        for node in ast.walk(source_tree()):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)

        roots = {module.split(".")[0] for module in modules}
        assert "fastapi" not in roots
        assert "sqlalchemy" not in roots
        assert "aioboto3" not in roots
        assert "botocore" not in roots
        # Nothing from `app` at all: a key layout needs no Anvex vocabulary, not even an
        # error class — a bad segment is a plain `ValueError` the service translates.
        assert {module for module in modules if module.startswith("app")} == set()

    def test_module_never_reads_a_clock_or_entropy(self) -> None:
        """The whole reason a key can be asserted in full."""
        forbidden = {"now", "utcnow", "today", "time", "monotonic", "uuid4", "uuid1", "token_hex"}
        offenders = []
        for node in ast.walk(source_tree()):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in forbidden:
                offenders.append(name)

        assert offenders == [], (
            f"domain/storage.py must take the clock and the token as parameters: {offenders}"
        )

    def test_module_source_mentions_no_clock_read(self) -> None:
        source = Path(storage.__file__).read_text(encoding="utf-8")
        assert "utcnow" not in source
        assert ".now(" not in source
        assert "get_settings" not in source


class TestNormaliseExtension:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("csv", "csv"), (".CSV", "csv"), ("  .Json  ", "json"), ("PARQUET", "parquet")],
    )
    def test_it_strips_the_dot_and_folds_case(self, raw: str, expected: str) -> None:
        assert normalise_extension(raw) == expected

    @pytest.mark.parametrize("raw", ["", ".", "tar.gz", "../x", "a" * 13, "cs v", "csv/"])
    def test_it_refuses_anything_that_is_not_a_short_alphanumeric_run(self, raw: str) -> None:
        with pytest.raises(ValueError, match="not a usable file extension"):
            normalise_extension(raw)


class TestSlugify:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Quarterly Report", "quarterly-report"),
            ("  AAPL / 2024  ", "aapl-2024"),
            ("a___b", "a-b"),
            ("Ünïcödé", "n-c-d"),
            ("--edges--", "edges"),
        ],
    )
    def test_it_collapses_to_the_allowed_run(self, raw: str, expected: str) -> None:
        assert slugify(raw) == expected

    def test_it_truncates_and_then_trims_so_a_slug_never_ends_in_a_dash(self) -> None:
        # 59 characters, then a dash at position 60: a naive truncation leaves the dash.
        raw = f"{'a' * (MAX_SLUG_LENGTH - 1)} tail"

        slug = slugify(raw)

        assert len(slug) <= MAX_SLUG_LENGTH
        assert not slug.endswith("-")

    @pytest.mark.parametrize("raw", ["", "   ", "???", "///"])
    def test_it_refuses_rather_than_inventing_a_name(self, raw: str) -> None:
        """Substituting "untitled" would put an unfindable object in the bucket."""
        with pytest.raises(ValueError, match="no characters usable"):
            slugify(raw)


class TestExportKey:
    def test_the_whole_key(self) -> None:
        key = export_key(
            resource="watchlist",
            owner_id=OWNER,
            name="Quarterly Report",
            extension="CSV",
            now=NOW,
            unique=UNIQUE,
        )

        assert key == (f"exports/watchlist/{OWNER}/2024/03/01/090507-quarterly-report-abcd1234.csv")

    def test_it_starts_with_the_day_prefix_it_would_be_listed_under(self) -> None:
        key = export_key(
            resource="watchlist",
            owner_id=OWNER,
            name="r",
            extension="csv",
            now=NOW,
            unique=UNIQUE,
        )

        assert key.startswith(export_prefix_for_day(resource="watchlist", owner_id=OWNER, day=NOW))
        assert key.startswith(export_prefix_for_owner(resource="watchlist", owner_id=OWNER))

    def test_the_same_second_and_the_same_name_still_give_two_objects(self) -> None:
        """`PutObject` has no "fail if exists"; uniqueness has to be in the name."""
        common = {
            "resource": "watchlist",
            "owner_id": OWNER,
            "name": "report",
            "extension": "csv",
            "now": NOW,
        }

        assert export_key(**common, unique="aaaaaaaa") != export_key(**common, unique="bbbbbbbb")

    def test_the_partition_follows_the_instant_not_the_local_calendar(self) -> None:
        """The same instant in two zones is one object's day, not two."""
        here = export_key(
            resource="w", owner_id=OWNER, name="r", extension="csv", now=NOW, unique=UNIQUE
        )
        there = export_key(
            resource="w",
            owner_id=OWNER,
            name="r",
            extension="csv",
            now=NOW_ELSEWHERE,
            unique=UNIQUE,
        )

        # `strftime` renders the *local* fields, so a -05:00 rendering of 09:05 UTC is the
        # previous day. That is precisely why the caller is required to pass UTC, and why
        # this test asserts the difference rather than pretending it does not exist.
        assert here != there
        assert here.startswith(f"exports/w/{OWNER}/2024/03/01/")
        assert there.startswith(f"exports/w/{OWNER}/2024/02/29/")

    def test_a_naive_now_is_refused(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            export_key(
                resource="w", owner_id=OWNER, name="r", extension="csv", now=NAIVE, unique=UNIQUE
            )

    @pytest.mark.parametrize(
        "resource", ["", "reports and more", "../etc", "-lead", "under_score", "x" * 33]
    )
    def test_a_resource_segment_is_validated_not_slugified(self, resource: str) -> None:
        """A prefix a lifecycle policy filters on must not be silently rewritten.

        Contrast :class:`TestSlugify`: ``"reports and more"`` would happily become
        ``reports-and-more``, and that is exactly the silent rewrite this refuses.
        """
        with pytest.raises(ValueError, match="not a usable resource segment"):
            export_key(
                resource=resource,
                owner_id=OWNER,
                name="r",
                extension="csv",
                now=NOW,
                unique=UNIQUE,
            )

    def test_case_and_surrounding_space_are_folded_because_they_change_no_prefix(self) -> None:
        """The one normalisation allowed: it cannot turn one valid prefix into another."""
        assert export_prefix_for_owner(resource="  Stock-Data ", owner_id=OWNER) == (
            export_prefix_for_owner(resource="stock-data", owner_id=OWNER)
        )

    def test_a_traversing_name_cannot_climb_out_of_the_prefix(self) -> None:
        key = export_key(
            resource="w",
            owner_id=OWNER,
            name="../../etc/passwd",
            extension="csv",
            now=NOW,
            unique=UNIQUE,
        )

        assert key.startswith(export_prefix_for_owner(resource="w", owner_id=OWNER))
        assert ".." not in key
        assert key.endswith("-etc-passwd-abcd1234.csv")

    def test_an_over_long_name_cannot_push_the_key_past_the_s3_limit(self) -> None:
        key = export_key(
            resource="w",
            owner_id=OWNER,
            name="x" * 5_000,
            extension="csv",
            now=NOW,
            unique=UNIQUE,
        )

        assert len(key.encode("utf-8")) <= MAX_KEY_BYTES


class TestPrefixes:
    def test_an_owner_prefix_ends_in_a_slash_so_it_cannot_match_a_sibling(self) -> None:
        prefix = export_prefix_for_owner(resource="watchlist", owner_id=OWNER)

        assert prefix == f"{EXPORTS_PREFIX}/watchlist/{OWNER}/"
        assert prefix.endswith("/")
        # The point of the trailing slash: without it this comparison would pass.
        assert not f"{EXPORTS_PREFIX}/watchlist/{OWNER}0/x".startswith(prefix)

    def test_a_day_prefix_is_zero_padded_so_lexical_order_is_chronological(self) -> None:
        january = export_prefix_for_day(
            resource="w", owner_id=OWNER, day=datetime(2024, 1, 2, tzinfo=UTC)
        )
        november = export_prefix_for_day(
            resource="w", owner_id=OWNER, day=datetime(2024, 11, 2, tzinfo=UTC)
        )

        assert january.endswith("2024/01/02/")
        assert january < november

    def test_a_naive_day_is_refused(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            export_prefix_for_day(resource="w", owner_id=OWNER, day=NAIVE)


class TestOwnerOfExportKey:
    def test_it_reads_back_what_export_key_wrote(self) -> None:
        key = export_key(
            resource="w", owner_id=OWNER, name="r", extension="csv", now=NOW, unique=UNIQUE
        )

        assert owner_of_export_key(key) == OWNER

    def test_a_different_owner_is_not_this_one(self) -> None:
        key = export_key(
            resource="w", owner_id=OTHER_OWNER, name="r", extension="csv", now=NOW, unique=UNIQUE
        )

        assert owner_of_export_key(key) != OWNER

    @pytest.mark.parametrize(
        "key",
        [
            "",
            "exports",
            "exports/w/not-a-uuid/2024/03/01/a.csv",
            "elsewhere/w/11111111-2222-3333-4444-555555555555/2024/03/01/a.csv",
            "exports/w/11111111-2222-3333-4444-555555555555",
        ],
    )
    def test_anything_that_is_not_an_export_key_owns_nothing(self, key: str) -> None:
        assert owner_of_export_key(key) is None


class TestValidateKey:
    def test_a_good_key_is_returned_unchanged(self) -> None:
        assert validate_key("exports/w/a/b.csv") == "exports/w/a/b.csv"

    @pytest.mark.parametrize(
        ("key", "why"),
        [
            ("", "may not be empty"),
            ("/leading", "may not start"),
            ("a\\b", "backslash"),
            ("a/../b", "empty or relative"),
            ("a/./b", "empty or relative"),
            ("a//b", "empty or relative"),
            ("a/b/", "empty or relative"),
            ("a\nb", "control characters"),
            ("a\x7fb", "control characters"),
        ],
    )
    def test_the_refusals(self, key: str, why: str) -> None:
        with pytest.raises(ValueError, match=why):
            validate_key(key)

    def test_the_length_limit_counts_bytes_not_characters(self) -> None:
        """900 multibyte characters pass a character count and fail at the vendor."""
        key = "é" * 900

        assert len(key) < MAX_KEY_BYTES
        with pytest.raises(ValueError, match="UTF-8 bytes"):
            validate_key(key)


class TestContentTypeFor:
    @pytest.mark.parametrize(("value", "expected"), sorted(CONTENT_TYPES.items()))
    def test_every_known_extension_maps_to_itself(self, value: str, expected: str) -> None:
        assert content_type_for(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            "csv",
            ".csv",
            "CSV",
            "report.csv",
            "exports/w/1/2024/03/01/090507-report-abcd1234.csv",
        ],
    )
    def test_it_accepts_an_extension_a_filename_or_a_whole_key(self, value: str) -> None:
        assert content_type_for(value) == "text/csv; charset=utf-8"

    @pytest.mark.parametrize("value", ["", "exe", "no-extension-here", "a.", "a.tar.gz.??"])
    def test_anything_unrecognised_downloads_rather_than_being_guessed(self, value: str) -> None:
        assert content_type_for(value) == DEFAULT_CONTENT_TYPE

    def test_csv_is_utf8_because_a_bare_text_csv_is_read_as_latin_1(self) -> None:
        assert content_type_for("csv") == "text/csv; charset=utf-8"

    def test_the_default_is_never_a_type_a_browser_will_render(self) -> None:
        assert DEFAULT_CONTENT_TYPE == "application/octet-stream"


class TestResolveDownloadTtl:
    def test_none_takes_the_default(self) -> None:
        assert resolve_download_ttl() == int(DEFAULT_DOWNLOAD_TTL.total_seconds())
        assert resolve_download_ttl(None) == int(DEFAULT_DOWNLOAD_TTL.total_seconds())

    def test_a_value_in_range_is_returned_in_whole_seconds(self) -> None:
        assert resolve_download_ttl(timedelta(minutes=5)) == 300
        assert resolve_download_ttl(timedelta(seconds=90.7)) == 90

    @pytest.mark.parametrize(
        ("requested", "expected"),
        [
            (timedelta(days=7), MAX_DOWNLOAD_TTL),
            (timedelta(hours=1, seconds=1), MAX_DOWNLOAD_TTL),
            (timedelta(seconds=0), MIN_DOWNLOAD_TTL),
            (timedelta(seconds=-60), MIN_DOWNLOAD_TTL),
        ],
    )
    def test_out_of_range_is_clamped_rather_than_refused(
        self, requested: timedelta, expected: timedelta
    ) -> None:
        """A caller with no HTTP request to reject still gets a usable link."""
        assert resolve_download_ttl(requested) == int(expected.total_seconds())

    def test_a_negative_ttl_never_becomes_an_already_expired_signature(self) -> None:
        assert resolve_download_ttl(timedelta(seconds=-1)) > 0

    def test_the_band_is_ordered(self) -> None:
        assert MIN_DOWNLOAD_TTL < DEFAULT_DOWNLOAD_TTL < MAX_DOWNLOAD_TTL
