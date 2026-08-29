"""Behaviour of ``app/clients/alphavantage.py`` against a ``respx``-mocked vendor.

``CLAUDE.md`` §6 puts client tests in ``tests/integration/`` because a real
``httpx.AsyncClient`` is exercised end to end, but this module asks for no ``db_*`` fixture,
so it is not marked ``db`` and **runs unchanged with Docker stopped**.

**Nothing here has ever touched AlphaVantage.** Every payload below is *hand-built* from the
vendor's published ``TIME_SERIES_INTRADAY`` response shape — the ``"Meta Data"`` block, the
``"Time Series (5min)"`` map, the ``"1. open"``/``"5. volume"`` field names — with prices and
volumes chosen to make an assertion sharp (four decimal places that must survive, a fifth
that must not be invented). They are not captured traffic, no API key is configured, and
``mock_http`` refuses to let a request escape to the network.

The interesting half of this module is the pair of failures that arrive as ``200 OK``:
AlphaVantage signals a throttle with a JSON ``"Note"``/``"Information"`` body and an unknown
symbol with an ``"Error Message"``. The base class cannot see either — to it they are
perfectly good responses — so the tests below prove the *parser* catches them, and that it
does not mistake the ``"1. Information"`` key **inside** a healthy ``"Meta Data"`` block for
one of them.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from datetime import date, time
from decimal import Decimal
from typing import Any

import pytest
import respx
from pydantic import SecretStr, ValidationError
from structlog.testing import capture_logs

from app.clients.alphavantage import (
    AlphaVantageClient,
    IntradayCandle,
    IntradayInterval,
    IntradaySeries,
    time_series_key,
)
from app.clients.base import REDACTED
from app.domain.errors import AnvexError, ExternalServiceError
from app.settings import Settings

#: The credential that must never turn up in a log line or an exception. Not a real key.
API_KEY = "alphavantage-test-key-9000"

QUERY_URL = "https://www.alphavantage.co/query"

# ---------------------------------------------------------------------------------------
# Hand-built payloads
#
# Written from AlphaVantage's documented TIME_SERIES_INTRADAY response shape, not captured
# from the live API. The numbers are chosen, not observed: `186.1234` has the fourth decimal
# place ANV-7's NUMERIC(12,4) can hold and the old ETL's `round(…, 2)` would have destroyed.
# ---------------------------------------------------------------------------------------

#: Newest first, as the vendor lists them.
CANDLE_ROWS: dict[str, dict[str, str]] = {
    "2024-01-31 15:55:00": {
        "1. open": "186.1234",
        "2. high": "186.5000",
        "3. low": "185.9900",
        "4. close": "186.4200",
        "5. volume": "52815",
    },
    "2024-01-31 15:50:00": {
        "1. open": "185.7500",
        "2. high": "186.2000",
        "3. low": "185.7000",
        "4. close": "186.1200",
        "5. volume": "17204",
    },
    "2024-01-31 09:35:00": {
        "1. open": "184.0100",
        "2. high": "184.9900",
        "3. low": "183.8800",
        "4. close": "184.7700",
        "5. volume": "104556",
    },
}


def intraday_payload(
    rows: dict[str, Any] | None = None,
    *,
    interval: str = "5min",
    symbol: str = "IBM",
    timezone: str | None = "US/Eastern",
    meta: bool = True,
) -> dict[str, Any]:
    """A successful ``TIME_SERIES_INTRADAY`` body, hand-built."""
    payload: dict[str, Any] = {}
    if meta:
        payload["Meta Data"] = {
            # Note the key: a *good* payload carries "Information" nested in here, which is
            # exactly what a naive rate-limit check would trip over.
            "1. Information": (f"Intraday ({interval}) open, high, low, close prices and volume"),
            "2. Symbol": symbol,
            "3. Last Refreshed": "2024-01-31 15:55:00",
            "4. Interval": interval,
            "5. Output Size": "Full size",
            "6. Time Zone": timezone,
        }
    payload[time_series_key(interval)] = CANDLE_ROWS if rows is None else rows
    return payload


#: The classic per-minute throttle. AlphaVantage returns this with ``200 OK``.
NOTE_PAYLOAD: dict[str, str] = {
    "Note": (
        "Thank you for using Alpha Vantage! Our standard API call frequency is 5 calls "
        "per minute and 500 calls per day. Please visit premium plans if you would like "
        "to target a higher API call frequency."
    )
}

#: The current daily-cap throttle. Also ``200 OK``.
INFORMATION_PAYLOAD: dict[str, str] = {
    "Information": (
        "We have detected your API key and our standard API rate limit is 25 requests "
        "per day. Please subscribe to a premium plan to instantly remove all daily rate "
        "limits."
    )
}

#: What an unknown symbol gets. A 400 wearing a 200.
ERROR_MESSAGE_PAYLOAD: dict[str, str] = {
    "Error Message": (
        "Invalid API call. Please retry or visit the documentation for TIME_SERIES_INTRADAY."
    )
}


#: The row every "one field is wrong" case is built from.
GOOD_ROW: dict[str, str] = CANDLE_ROWS["2024-01-31 15:55:00"]


def a_row(overrides: dict[str, Any]) -> dict[str, Any]:
    """:data:`GOOD_ROW` with selected vendor fields replaced by something unusable."""
    return {**GOOD_ROW, **overrides}


def a_row_without(key: str) -> dict[str, Any]:
    """:data:`GOOD_ROW` with one of the vendor's five fields simply absent."""
    return {name: value for name, value in GOOD_ROW.items() if name != key}


# ---------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """The shared fixture with one field pinned — ``CLAUDE.md`` §6's documented idiom."""
    return settings.model_copy(update={"alphavantage_api_key": SecretStr(API_KEY)})


@pytest.fixture
def sleeps() -> list[float]:
    """Every delay the retry loop asked for. Nothing here should ever ask for one."""
    return []


@pytest.fixture
async def client(settings: Settings, sleeps: list[float]) -> AsyncIterator[AlphaVantageClient]:
    async def record(delay: float) -> None:
        sleeps.append(delay)

    vendor = AlphaVantageClient(settings, sleep=record, jitter=lambda: 0.0)
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
    async def test_it_returns_typed_candles_never_a_response(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        route = mock_http.get(QUERY_URL).respond(200, json=intraday_payload())

        series = await client.fetch_intraday("IBM", month="2024-01")

        assert isinstance(series, IntradaySeries)
        assert route.call_count == 1
        assert len(series.candles) == 3
        assert all(isinstance(candle, IntradayCandle) for candle in series.candles)

    async def test_the_vendors_ohlcv_keys_are_mapped_onto_named_fields(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        """The one piece of the old ETL genuinely worth porting."""
        mock_http.get(QUERY_URL).respond(200, json=intraday_payload())

        series = await client.fetch_intraday("IBM")

        assert series.candles[0] == IntradayCandle(
            date=date(2024, 1, 31),
            time=time(15, 55),
            open=Decimal("186.1234"),
            high=Decimal("186.5000"),
            low=Decimal("185.9900"),
            close=Decimal("186.4200"),
            volume=52815,
        )

    async def test_the_datetime_index_is_split_into_a_date_and_a_time(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        """The old ETL's ``[d.date() for d in df['datetime']]``, without the DataFrame."""
        mock_http.get(QUERY_URL).respond(200, json=intraday_payload())

        series = await client.fetch_intraday("IBM")

        assert [candle.time for candle in series.candles] == [
            time(15, 55),
            time(15, 50),
            time(9, 35),
        ]
        assert {candle.date for candle in series.candles} == {date(2024, 1, 31)}

    async def test_the_vendors_ordering_is_preserved(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        """Reordering is a transformation; this layer reports rather than transforms."""
        mock_http.get(QUERY_URL).respond(200, json=intraday_payload())

        series = await client.fetch_intraday("IBM")

        assert [str(candle.time) for candle in series.candles] == [
            key.split(" ")[1] for key in CANDLE_ROWS
        ]

    async def test_the_metadata_that_gives_the_times_meaning_is_carried(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        """Without the zone, ``time(15, 55)`` means nothing to ANV-22's window rule."""
        mock_http.get(QUERY_URL).respond(200, json=intraday_payload())

        series = await client.fetch_intraday("ibm")

        assert series.symbol == "IBM"
        assert series.timezone == "US/Eastern"
        assert series.interval is IntradayInterval.FIVE_MINUTES

    async def test_absent_metadata_is_not_a_failure(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        """Metadata is advisory. The bars are the payload."""
        mock_http.get(QUERY_URL).respond(200, json=intraday_payload(meta=False))

        series = await client.fetch_intraday("IBM")

        assert series.timezone is None
        assert series.symbol == "IBM"  # falls back to what was asked for
        assert len(series.candles) == 3

    async def test_a_blank_time_zone_is_none_rather_than_an_empty_string(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(QUERY_URL).respond(200, json=intraday_payload(timezone="   "))

        series = await client.fetch_intraday("IBM")

        assert series.timezone is None

    async def test_an_empty_series_is_an_empty_result_not_an_error(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        """A window with no trading in it is an answer, not a failure. What to do about an
        empty result is ANV-22's decision, and it cannot make it if this raises.
        """
        mock_http.get(QUERY_URL).respond(200, json=intraday_payload({}))

        series = await client.fetch_intraday("IBM", month="2024-01")

        assert series.candles == ()
        assert series.symbol == "IBM"
        assert series.timezone == "US/Eastern"

    @pytest.mark.parametrize("interval", list(IntradayInterval))
    async def test_every_interval_reads_its_own_series_key(
        self,
        client: AlphaVantageClient,
        mock_http: respx.MockRouter,
        interval: IntradayInterval,
    ) -> None:
        """``"Time Series (15min)"`` is a different key from ``"Time Series (5min)"``."""
        mock_http.get(QUERY_URL).respond(200, json=intraday_payload(interval=interval))

        series = await client.fetch_intraday("IBM", interval=interval)

        assert series.interval is interval
        assert len(series.candles) == 3


# ---------------------------------------------------------------------------------------
# precision — the rounding decision
# ---------------------------------------------------------------------------------------


class TestPrecision:
    async def test_prices_are_not_rounded(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        """The old ETL's ``round(…, 2)`` matched a NUMERIC(8,2) column that no longer
        exists. ANV-7 stores NUMERIC(12,4), and rounding is lossy and irreversible — so the
        vendor client is the worst place to do it. Quantising is ANV-22's call.
        """
        mock_http.get(QUERY_URL).respond(200, json=intraday_payload())

        series = await client.fetch_intraday("IBM")

        assert series.candles[0].open == Decimal("186.1234")

    async def test_a_price_never_passes_through_a_float(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        """``Decimal(float("0.1"))`` is not ``Decimal("0.1")``; parsing the string is."""
        rows = {"2024-01-31 15:55:00": a_row({"1. open": "0.1"})}
        mock_http.get(QUERY_URL).respond(200, json=intraday_payload(rows))

        series = await client.fetch_intraday("IBM")

        assert series.candles[0].open == Decimal("0.1")
        assert str(series.candles[0].open) == "0.1"

    async def test_trailing_zeros_survive_so_the_scale_is_the_vendors(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(QUERY_URL).respond(200, json=intraday_payload())

        series = await client.fetch_intraday("IBM")

        assert str(series.candles[0].high) == "186.5000"


# ---------------------------------------------------------------------------------------
# the request this client makes
# ---------------------------------------------------------------------------------------


class TestTheRequest:
    async def test_the_documented_query_parameters_are_sent(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        route = mock_http.get(QUERY_URL).respond(200, json=intraday_payload())

        await client.fetch_intraday("IBM", month="2024-01")

        params = route.calls.last.request.url.params
        assert params["function"] == "TIME_SERIES_INTRADAY"
        assert params["symbol"] == "IBM"
        assert params["interval"] == "5min"
        assert params["outputsize"] == "full"
        assert params["month"] == "2024-01"

    async def test_the_credential_is_actually_sent(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        """Redaction must not be achieved by never sending the key."""
        route = mock_http.get(QUERY_URL).respond(200, json=intraday_payload())

        await client.fetch_intraday("IBM")

        assert route.calls.last.request.url.params["apikey"] == API_KEY

    async def test_no_month_means_no_month_parameter(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        """Sending an empty ``month=`` is not the same request as omitting it."""
        route = mock_http.get(QUERY_URL).respond(200, json=intraday_payload())

        await client.fetch_intraday("IBM")

        assert "month" not in route.calls.last.request.url.params

    async def test_the_symbol_is_passed_through_exactly_as_given(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        """``CLAUDE.md`` §4: normalising an identifier is the *service's* job."""
        route = mock_http.get(QUERY_URL).respond(200, json=intraday_payload())

        await client.fetch_intraday("  ibm  ")

        assert route.calls.last.request.url.params["symbol"] == "  ibm  "

    async def test_the_trading_hours_rule_is_not_smuggled_into_the_request(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        """``extended_hours=false`` would move ANV-22's 08:05-17:00 window into the URL,
        where it would be invisible to the domain tests that are supposed to own it.
        """
        route = mock_http.get(QUERY_URL).respond(200, json=intraday_payload())

        await client.fetch_intraday("IBM")

        assert "extended_hours" not in route.calls.last.request.url.params

    async def test_nothing_here_ever_sleeps_to_stay_under_a_quota(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter, sleeps: list[float]
    ) -> None:
        """The old ETL's ``time.sleep(10)`` between calls belongs to the fan-out job."""
        mock_http.get(QUERY_URL).respond(200, json=intraday_payload())

        await client.fetch_intraday("IBM")
        await client.fetch_intraday("MSFT")

        assert sleeps == []


# ---------------------------------------------------------------------------------------
# the trap: a rate limit that arrives as a 200
# ---------------------------------------------------------------------------------------


class TestARateLimitDisguisedAsSuccess:
    @pytest.mark.parametrize(
        ("label", "payload"),
        [("note", NOTE_PAYLOAD), ("information", INFORMATION_PAYLOAD)],
    )
    async def test_a_200_throttle_body_is_an_external_service_error(
        self,
        client: AlphaVantageClient,
        mock_http: respx.MockRouter,
        label: str,
        payload: dict[str, str],
    ) -> None:
        """The base sees 200 + valid JSON and is satisfied. The parser must not be."""
        mock_http.get(QUERY_URL).respond(200, json=payload)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_intraday("IBM")

        assert details_of(caught)["reason"] == "rate_limited"
        assert details_of(caught)["service"] == "alphavantage"

    @pytest.mark.parametrize("payload", [NOTE_PAYLOAD, INFORMATION_PAYLOAD])
    async def test_the_message_is_the_same_one_a_429_would_have_produced(
        self,
        client: AlphaVantageClient,
        mock_http: respx.MockRouter,
        payload: dict[str, str],
    ) -> None:
        """Raised through the base's own constructor, so a consumer cannot tell a 200-body
        throttle from a real 429 — which is the point, because they mean the same thing.
        """
        mock_http.get(QUERY_URL).respond(200, json=payload)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_intraday("IBM")

        assert caught.value.message == "The upstream service 'alphavantage' is rate limiting Anvex."

    async def test_it_carries_no_invented_attempt_count(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        """The retry loop *succeeded*; there is no attempt count for this failure, so the
        key is absent rather than a fabricated ``1``.
        """
        mock_http.get(QUERY_URL).respond(200, json=NOTE_PAYLOAD)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_intraday("IBM")

        assert "attempts" not in details_of(caught)

    async def test_a_throttle_body_is_never_forwarded_to_the_caller(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        """``CLAUDE.md`` §3: ``details`` never carries the vendor's own output."""
        mock_http.get(QUERY_URL).respond(200, json=NOTE_PAYLOAD)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_intraday("IBM")

        rendered = json.dumps(details_of(caught), default=str) + str(caught.value)
        assert "premium" not in rendered
        assert "5 calls per minute" not in rendered

    async def test_the_call_is_not_repeated(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter, sleeps: list[float]
    ) -> None:
        """Asking a throttled vendor twice more is how one throttle becomes three."""
        route = mock_http.get(QUERY_URL).respond(200, json=NOTE_PAYLOAD)

        with pytest.raises(ExternalServiceError):
            await client.fetch_intraday("IBM")

        assert route.call_count == 1
        assert sleeps == []

    async def test_information_inside_meta_data_is_not_a_throttle(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        """A healthy payload carries ``"1. Information"`` *inside* ``"Meta Data"``. Checking
        for the key anywhere rather than at the top level would fail every good response.
        """
        payload = intraday_payload()
        assert "1. Information" in payload["Meta Data"]

        mock_http.get(QUERY_URL).respond(200, json=payload)

        series = await client.fetch_intraday("IBM")

        assert len(series.candles) == 3


# ---------------------------------------------------------------------------------------
# a vendor rejection, also disguised as success
# ---------------------------------------------------------------------------------------


class TestAVendorRejection:
    async def test_an_error_message_body_is_a_client_error(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        """What an unknown symbol gets. Permanent, so classified as the 400 it should be."""
        mock_http.get(QUERY_URL).respond(200, json=ERROR_MESSAGE_PAYLOAD)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_intraday("NOSUCHTICKER")

        assert details_of(caught)["reason"] == "client_error"
        assert caught.value.message == "The upstream service 'alphavantage' rejected the request."

    async def test_the_rejection_is_not_retried(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter, sleeps: list[float]
    ) -> None:
        route = mock_http.get(QUERY_URL).respond(200, json=ERROR_MESSAGE_PAYLOAD)

        with pytest.raises(ExternalServiceError):
            await client.fetch_intraday("NOSUCHTICKER")

        assert route.call_count == 1
        assert sleeps == []

    async def test_the_vendors_wording_is_not_forwarded(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(QUERY_URL).respond(200, json=ERROR_MESSAGE_PAYLOAD)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_intraday("NOSUCHTICKER")

        assert "Invalid API call" not in str(caught.value)
        assert "NOSUCHTICKER" not in json.dumps(details_of(caught), default=str)


# ---------------------------------------------------------------------------------------
# malformed payloads
# ---------------------------------------------------------------------------------------


class TestAMalformedPayload:
    """The old ETL's ``pd.to_numeric(errors="coerce")`` turned each of these into a silent
    ``NaN`` that then went into the database. Every one of them is a failure here.
    """

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("the time series key is missing", {"Meta Data": {"2. Symbol": "IBM"}}),
            ("the whole body is empty", {}),
            ("the body is a list", [{"1. open": "1"}]),
            ("the body is a bare string", "OK"),
            ("the series is not a mapping", {"Time Series (5min)": []}),
            ("the series is null", {"Time Series (5min)": None}),
            (
                "the interval does not match the key",
                {"Time Series (1min)": CANDLE_ROWS},
            ),
        ],
    )
    async def test_a_body_without_the_expected_series_is_malformed(
        self,
        client: AlphaVantageClient,
        mock_http: respx.MockRouter,
        label: str,
        payload: Any,
    ) -> None:
        mock_http.get(QUERY_URL).respond(200, json=payload)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_intraday("IBM")

        assert details_of(caught)["reason"] == "malformed_response", label

    @pytest.mark.parametrize(
        ("label", "row"),
        [
            ("a non-numeric price", a_row({"1. open": "not-a-number"})),
            ("an empty price", a_row({"2. high": ""})),
            ("a missing price key", a_row_without("3. low")),
            ("a missing volume key", a_row_without("5. volume")),
            ("a null price", a_row({"4. close": None})),
            ("a boolean price", a_row({"1. open": True})),
            ("a NaN price", a_row({"1. open": "NaN"})),
            ("an infinite price", a_row({"2. high": "Infinity"})),
            ("a fractional volume", a_row({"5. volume": "1234.5"})),
            ("a non-numeric volume", a_row({"5. volume": "lots"})),
            ("the row is not a mapping", "186.42"),
            ("the row is null", None),
        ],
    )
    async def test_a_bad_candle_fails_the_whole_fetch(
        self,
        client: AlphaVantageClient,
        mock_http: respx.MockRouter,
        label: str,
        row: Any,
    ) -> None:
        """One bad row stops the load, exactly as ``app/data/loader.py`` does: a partially
        valid batch is not a batch.
        """
        payload = intraday_payload({"2024-01-31 15:55:00": row})
        mock_http.get(QUERY_URL).respond(200, json=payload)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_intraday("IBM")

        assert details_of(caught)["reason"] == "malformed_response", label

    @pytest.mark.parametrize(
        "timestamp",
        [
            "2024-01-31",  # a date with no time
            "31/01/2024 15:55:00",  # the wrong order
            "2024-13-31 15:55:00",  # no thirteenth month
            "2024-01-31T15:55:00",  # ISO, which is not what this vendor sends
            "",
        ],
    )
    async def test_a_bad_timestamp_fails_the_whole_fetch(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter, timestamp: str
    ) -> None:
        payload = intraday_payload({timestamp: CANDLE_ROWS["2024-01-31 15:55:00"]})
        mock_http.get(QUERY_URL).respond(200, json=payload)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_intraday("IBM")

        assert details_of(caught)["reason"] == "malformed_response"

    async def test_a_malformed_payload_is_not_retried(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter, sleeps: list[float]
    ) -> None:
        """A vendor sending nonsense is broken, not blipping."""
        route = mock_http.get(QUERY_URL).respond(200, json={"Meta Data": {}})

        with pytest.raises(ExternalServiceError):
            await client.fetch_intraday("IBM")

        assert route.call_count == 1
        assert sleeps == []

    async def test_every_parse_failure_is_still_the_layers_one_exception(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        """No ``ValueError``, no ``pydantic.ValidationError``, no ``KeyError`` escapes —
        ``CLAUDE.md`` §3 makes ``ExternalServiceError`` the layer's single exit (→ 502).
        """
        mock_http.get(QUERY_URL).respond(
            200, json=intraday_payload({"2024-01-31 15:55:00": a_row({"1. open": "x"})})
        )

        with pytest.raises(AnvexError) as caught:
            await client.fetch_intraday("IBM")

        assert isinstance(caught.value, ExternalServiceError)
        assert caught.value.code == "external_service_error"


# ---------------------------------------------------------------------------------------
# the base still owns the transport failures
# ---------------------------------------------------------------------------------------


class TestTheBaseStillOwnsTransport:
    @pytest.mark.parametrize(
        ("status", "reason"),
        [(500, "server_error"), (401, "client_error"), (429, "rate_limited")],
    )
    async def test_a_real_status_code_is_classified_by_the_base(
        self,
        client: AlphaVantageClient,
        mock_http: respx.MockRouter,
        status: int,
        reason: str,
    ) -> None:
        """This subclass writes no status check of its own; it inherits every one."""
        mock_http.get(QUERY_URL).respond(status)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_intraday("IBM")

        assert details_of(caught)["reason"] == reason
        assert details_of(caught)["attempts"] >= 1

    async def test_a_body_that_is_not_json_is_the_bases_problem(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(QUERY_URL).respond(200, text="<html>maintenance</html>")

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_intraday("IBM")

        assert details_of(caught)["reason"] == "malformed_response"


# ---------------------------------------------------------------------------------------
# the credential
# ---------------------------------------------------------------------------------------


class TestTheApiKeyNeverEscapes:
    """``apikey`` travels in the query string, which is the part of a request that gets
    logged. ANV-17's redaction does the work; these assert it still holds for *this* vendor,
    including on the two failure paths that are unique to it.
    """

    async def test_no_log_line_from_a_successful_fetch_contains_the_key(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(QUERY_URL).respond(200, json=intraday_payload())

        with capture_logs() as entries:
            await client.fetch_intraday("IBM", month="2024-01")

        assert entries
        assert API_KEY not in json.dumps(entries, default=str)

    async def test_the_logged_url_is_redacted_but_still_diagnostic(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(QUERY_URL).respond(200, json=intraday_payload())

        with capture_logs() as entries:
            await client.fetch_intraday("IBM", month="2024-01")

        logged = json.dumps(entries, default=str)
        assert f"apikey={REDACTED}" in logged
        assert "function=TIME_SERIES_INTRADAY" in logged
        assert "symbol=IBM" in logged

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("note", NOTE_PAYLOAD),
            ("information", INFORMATION_PAYLOAD),
            ("error message", ERROR_MESSAGE_PAYLOAD),
            ("malformed", {"Meta Data": {}}),
        ],
    )
    async def test_the_200_failure_paths_never_log_the_key(
        self,
        client: AlphaVantageClient,
        mock_http: respx.MockRouter,
        label: str,
        payload: dict[str, Any],
    ) -> None:
        """These are the paths ANV-17 could not have covered: they are this module's."""
        mock_http.get(QUERY_URL).respond(200, json=payload)

        with capture_logs() as entries, pytest.raises(ExternalServiceError):
            await client.fetch_intraday("IBM")

        assert entries
        assert API_KEY not in json.dumps(entries, default=str)

    @pytest.mark.parametrize(
        "payload", [NOTE_PAYLOAD, INFORMATION_PAYLOAD, ERROR_MESSAGE_PAYLOAD, {"Meta Data": {}}]
    )
    async def test_the_exception_carries_nothing_derived_from_the_key(
        self, client: AlphaVantageClient, mock_http: respx.MockRouter, payload: dict[str, Any]
    ) -> None:
        """``details`` is serialised straight to an API consumer — ``CLAUDE.md`` §4."""
        mock_http.get(QUERY_URL).respond(200, json=payload)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_intraday("IBM")

        error = caught.value
        assert API_KEY not in str(error)
        assert API_KEY not in repr(error)
        assert API_KEY not in json.dumps(error.details, default=str)

    async def test_the_key_is_not_readable_off_the_client(self, client: AlphaVantageClient) -> None:
        """It stays a ``SecretStr``: the base unwraps it per request and stores nothing."""
        assert isinstance(client._api_key, SecretStr)
        assert API_KEY not in repr(client._api_key)


# ---------------------------------------------------------------------------------------
# the parser, reached directly
# ---------------------------------------------------------------------------------------


class TestTheParserInIsolation:
    """The parsing is the ported part, so it is also tested without a socket in front of
    it — the same payloads, one fewer moving part between the assertion and the rule.
    """

    def _parse(self, settings: Settings, payload: Any) -> IntradaySeries:
        return AlphaVantageClient(settings)._parse_intraday(
            payload, symbol="IBM", interval=IntradayInterval.FIVE_MINUTES
        )

    def test_a_good_payload_parses(self, settings: Settings) -> None:
        series = self._parse(settings, intraday_payload())

        assert len(series.candles) == 3

    @pytest.mark.parametrize("payload", [NOTE_PAYLOAD, INFORMATION_PAYLOAD])
    def test_a_throttle_body_raises_without_any_http(
        self, settings: Settings, payload: dict[str, str]
    ) -> None:
        with pytest.raises(ExternalServiceError) as caught:
            self._parse(settings, payload)

        assert caught.value.details["reason"] == "rate_limited"

    def test_a_volume_written_as_a_whole_float_is_accepted(self, settings: Settings) -> None:
        """``"52815.0"`` is the same number; ``"1234.5"`` is not a volume."""
        series = self._parse(
            settings, intraday_payload({"2024-01-31 15:55:00": a_row({"5. volume": "52815.0"})})
        )

        assert series.candles[0].volume == 52815

    def test_a_timestamp_that_is_not_even_a_string_is_malformed(self, settings: Settings) -> None:
        """Unreachable over HTTP — a JSON object key is always a string — but reachable by
        a caller handing the parser a dict, and the alternative is an ``AttributeError``
        escaping the layer that promises exactly one exception type.
        """
        with pytest.raises(ExternalServiceError) as caught:
            self._parse(settings, intraday_payload({20240131155500: GOOD_ROW}))

        assert caught.value.details["reason"] == "malformed_response"

    def test_whitespace_around_a_value_is_tolerated(self, settings: Settings) -> None:
        series = self._parse(
            settings,
            intraday_payload({" 2024-01-31 15:55:00 ": a_row({"1. open": " 186.1234 "})}),
        )

        assert series.candles[0].open == Decimal("186.1234")
        assert series.candles[0].time == time(15, 55)


# ---------------------------------------------------------------------------------------
# the layer's shape
# ---------------------------------------------------------------------------------------


class TestTheSubclassStaysSmall:
    """``CLAUDE.md`` §3: a client sets two attributes, returns its credential, and writes
    one method per vendor operation. The AST sweep in ``tests/unit/test_clients_base.py``
    guards the imports; these guard the shape.
    """

    def test_it_names_its_vendor_and_host(self) -> None:
        assert AlphaVantageClient.vendor == "alphavantage"
        assert AlphaVantageClient.base_url == "https://www.alphavantage.co"

    def test_the_candle_model_is_the_clients_own_not_an_api_schema(self) -> None:
        """``app/schemas/`` is forbidden on purpose — a vendor does not share Anvex's
        public shape, and it does not know what a ``stock_id`` is either.
        """
        assert IntradayCandle.__module__ == "app.clients.alphavantage"
        fields: Iterable[str] = IntradayCandle.model_fields
        assert set(fields) == {"date", "time", "open", "high", "low", "close", "volume"}
        assert "stock_id" not in fields
        assert not any(name.endswith("_price") for name in fields)

    def test_the_models_are_immutable(self) -> None:
        """A parsed vendor payload is a record of what was said, not a working buffer."""
        candle = IntradayCandle(
            date=date(2024, 1, 31),
            time=time(15, 55),
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=1,
        )

        with pytest.raises(ValidationError):
            candle.open = Decimal("2")

    def test_a_missing_key_is_an_empty_secret_not_a_crash(self) -> None:
        """A client is constructed in a dependency factory long before it is called."""
        vendor = AlphaVantageClient(Settings(alphavantage_api_key=SecretStr("")))

        assert vendor.auth_params()["apikey"].get_secret_value() == ""
