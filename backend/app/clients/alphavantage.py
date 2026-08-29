"""AlphaVantage — intraday OHLCV candles.

The vendor half of what the old ``AverageInvestorService`` ETL did. That script fetched a
month of five-minute bars, poured them into a ``pandas.DataFrame``, renamed the vendor's
``"1. open"`` keys to Postgres column names, rounded to two decimal places, split the
datetime index into ``date`` and ``time``, dropped everything outside 08:05-17:00, attached
a ``stock_id`` and wrote the frame straight to ``avg_inv.stock_data``.

**Only the middle of that belongs here.** ``CLAUDE.md`` §3: a client knows one vendor and
nothing about Anvex. So this module ports the key mapping, the numeric coercion and the
date/time split — and deliberately leaves behind:

* **the trading-hours window (08:05-17:00)**, which is an Anvex rule about which candles are
  interesting, not a fact about AlphaVantage. It belongs in ``app/domain/ingest.py``
  (ANV-22). For the same reason this module does **not** send ``extended_hours=false``:
  narrowing the vendor call would move that rule into the request and make it invisible.
* **"which months to fetch"**, likewise ANV-22's. ``month`` is a parameter here.
* **the ``time.sleep(10)`` between calls.** Proactive quota throttling (the free tier's five
  calls a minute) is a scheduling decision for the job that fans out — a request path cannot
  block to honour it, and a blocking sleep in an async client would stop the whole worker.
* **``stock_id`` and the column names.** A vendor does not know Anvex's tables, so the
  candle below is spelled in the vendor's own OHLCV vocabulary and ANV-22 maps it.
* **``errors="coerce"``.** pandas turned an unparseable price into ``NaN`` and carried on;
  a silently-NaN price that reaches a ``NUMERIC`` column is worse than a failed fetch, so a
  bad number here is :class:`~app.domain.errors.ExternalServiceError`.
* **``df.append()``**, which pandas 2 removed outright — the old code would not run today.

Rounding
--------

The old ETL rounded prices to two decimals. That number came from a ``NUMERIC(8,2)`` column
that no longer exists; ANV-7 stores ``NUMERIC(12,4)``. **This client does not round**, for
three reasons: rounding is lossy and irreversible, so the vendor client is the worst possible
place to do it; the precision it would round *to* is a property of ``app/models/`` which this
layer may not import (the AST sweep enforces that); and quantising to the storage scale is an
Anvex rule, so it belongs beside the other ingest rules in ANV-22. Prices are parsed from the
vendor's **strings** straight into :class:`~decimal.Decimal`, never through ``float``, so
nothing is lost on the way in and ANV-22 has the full value to quantise from.

No key, no request
------------------

``ALPHAVANTAGE_API_KEY`` defaults to a blank ``SecretStr``, so "not configured" is the state
of every fresh clone. :meth:`AlphaVantageClient._require_key` refuses before the request and
names the setting, per ``CLAUDE.md`` §3. **This was added by ANV-22, not by ANV-18**, and the
cost of its absence is worth recording: a keyless request is not refused by the vendor with
anything distinctive — AlphaVantage answers ``apikey=`` with a ``200`` and an
``"Error Message"``, which the parser below correctly reads as
:attr:`~app.clients.base.Failure.CLIENT_ERROR`. That is byte-identical to the answer for a
symbol that does not exist. A scheduled ingest fan-out would therefore spend one real round
trip per ticker per month, forever, reporting a missing environment variable as a bad
roster.

The trap: a rate limit that arrives as a 200
--------------------------------------------

AlphaVantage does not answer a throttled request with 429. It answers ``200 OK`` with a JSON
body containing ``"Note"`` (the classic five-calls-a-minute message) or ``"Information"`` (the
current daily-cap one) and no time series at all. To :class:`~app.clients.base.BaseHTTPClient`
that is a perfectly good response — status 2xx, valid JSON — so the base cannot see it and
:meth:`AlphaVantageClient._parse_intraday` has to.

``"Error Message"`` is the same trick for a *permanent* problem (an unknown symbol, a
malformed parameter), so it is classified as :attr:`~app.clients.base.Failure.CLIENT_ERROR` —
what a well-behaved vendor would have sent as a 400.

Both are raised through :meth:`~app.clients.base.BaseHTTPClient._error`, so a rate limit
reads identically in ``details.reason`` whether it arrived as a 429 or as a 200 body. There
is deliberately **no** ``_check_payload`` hook on the base: it would have exactly one caller
today, and its shape (return a ``Failure``? raise? take part in the retry budget?) would be
fixed by that single example. ``CLAUDE.md`` §4's rule for moving a pure rule downward is to
trigger on the *second* caller; if ANV-19 (NewsAPI) needs the same check, that is when the
generalisation is knowable.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar, Final

from pydantic import BaseModel, ConfigDict, SecretStr

from app.clients.base import BaseHTTPClient, Failure
from app.domain.errors import ExternalServiceError
from app.settings import Settings

# ---------------------------------------------------------------------------------------
# The vendor's wire vocabulary
# ---------------------------------------------------------------------------------------

#: The single endpoint every AlphaVantage function is dispatched through.
QUERY_PATH: Final[str] = "/query"

#: ``function=`` for intraday bars.
INTRADAY_FUNCTION: Final[str] = "TIME_SERIES_INTRADAY"

#: Always ``full``. ``compact`` truncates to the most recent 100 points, which for a
#: five-minute series is under two trading days — a truncation the ingest job would have to
#: work around, at exactly the same quota cost.
OUTPUT_SIZE: Final[str] = "full"

#: Top-level key holding the response's descriptive block.
META_DATA_KEY: Final[str] = "Meta Data"
#: Keys inside it that this client actually reads.
META_SYMBOL_KEY: Final[str] = "2. Symbol"
META_TIMEZONE_KEY: Final[str] = "6. Time Zone"

#: Top-level keys that mean "throttled", despite the 200. ``Note`` is the historical
#: per-minute message; ``Information`` is what the current daily cap returns. Checked at the
#: top level only — a *successful* payload also carries ``"1. Information"`` **inside**
#: ``Meta Data``, and mistaking that for a throttle would fail every good response.
RATE_LIMIT_KEYS: Final[tuple[str, ...]] = ("Note", "Information")

#: Top-level key meaning the vendor refused the request outright (unknown symbol, bad
#: parameter). A 400 wearing a 200.
ERROR_MESSAGE_KEY: Final[str] = "Error Message"

#: ``reason`` for the one failure that is Anvex's fault rather than the vendor's.
NOT_CONFIGURED: Final[str] = "not_configured"

#: The settings field an operator has to fill in. Named in ``details`` so the 502 is
#: actionable from the response body alone — it is a key *name* already committed to
#: ``.env.example``, never a value.
API_KEY_SETTING: Final[str] = "ALPHAVANTAGE_API_KEY"

#: The vendor's per-candle field names, mapped to this module's model fields. This mapping
#: *is* the thing worth porting from the old ETL — the numeric prefixes exist so the vendor's
#: JSON keys sort into OHLCV order, and they are stable across every AlphaVantage series.
OHLCV_KEYS: Final[Mapping[str, str]] = {
    "open": "1. open",
    "high": "2. high",
    "low": "3. low",
    "close": "4. close",
    "volume": "5. volume",
}

#: How AlphaVantage spells a bar's timestamp. Ported verbatim from the old ETL's
#: ``pd.to_datetime(..., format='%Y-%m-%d %H:%M:%S')``.
TIMESTAMP_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"


class IntradayInterval(StrEnum):
    """The five bar widths AlphaVantage offers for ``TIME_SERIES_INTRADAY``.

    An enum rather than a ``str`` because the value is used twice — once as a query
    parameter and once to build the response key — so a typo would otherwise surface as a
    "malformed response" from a vendor that did nothing wrong. ``CLAUDE.md`` §4: when a
    rule must not be forgotten, make the signature require it.
    """

    ONE_MINUTE = "1min"
    FIVE_MINUTES = "5min"
    FIFTEEN_MINUTES = "15min"
    THIRTY_MINUTES = "30min"
    SIXTY_MINUTES = "60min"


def time_series_key(interval: IntradayInterval | str) -> str:
    """The top-level key the bars live under, e.g. ``"Time Series (5min)"``."""
    return f"Time Series ({interval})"


# ---------------------------------------------------------------------------------------
# The typed result
# ---------------------------------------------------------------------------------------


class IntradayCandle(BaseModel):
    """One bar, in the vendor's own OHLCV words.

    Deliberately **not** an ``app.schemas`` model and deliberately not spelled
    ``open_price`` / ``stock_id``: those are Anvex's shapes, and a vendor does not share
    them. Mapping this onto :class:`~app.models.stock.StockData` is ANV-22's job.

    ``date`` and ``time`` are separate, as the old ETL had them and as ANV-7's table stores
    them. They are **exchange-local wall-clock**, carrying no zone of their own — the zone
    the vendor was quoting is on :attr:`IntradaySeries.timezone`.
    """

    model_config = ConfigDict(frozen=True)

    date: dt.date
    time: dt.time
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class IntradaySeries(BaseModel):
    """A whole intraday response: what was asked for, and the bars that came back.

    :attr:`timezone` is carried rather than dropped because it is the only thing that says
    what :attr:`IntradayCandle.time` *means*. ANV-22's trading-hours rule is expressed in
    exchange-local time, and without this it would have to hardcode ``US/Eastern`` and hope.
    """

    model_config = ConfigDict(frozen=True)

    #: As the vendor echoed it back, falling back to what was asked for.
    symbol: str
    #: The bar width, echoed from the request.
    interval: IntradayInterval
    #: e.g. ``"US/Eastern"``. ``None`` when the vendor omitted its metadata block.
    timezone: str | None
    #: In the order the vendor listed them — newest first, in practice. Reordering is a
    #: transformation, and this layer reports rather than transforms.
    candles: tuple[IntradayCandle, ...]


# ---------------------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------------------


class AlphaVantageClient(BaseHTTPClient):
    """AlphaVantage's HTTP surface. Two class attributes, one credential, one operation.

    Everything about *how* the request is made — timeouts, retry, redirect refusal, URL
    redaction, the single pooled ``httpx.AsyncClient``, and turning any failure into
    :class:`~app.domain.errors.ExternalServiceError` — is inherited from
    :class:`~app.clients.base.BaseHTTPClient` and is not re-derived here. What this module
    adds is the one thing the base cannot know: what an AlphaVantage payload means.

    Usage::

        async with AlphaVantageClient(settings) as vendor:
            series = await vendor.fetch_intraday("IBM", month="2024-01")
    """

    vendor: ClassVar[str] = "alphavantage"
    base_url: ClassVar[str] = "https://www.alphavantage.co"

    def __init__(self, settings: Settings, **kwargs: Any) -> None:
        # Stays a `SecretStr` for the client's whole life. The base unwraps it while
        # building one request and never stores the plaintext — see `CLAUDE.md` §3.
        self._api_key = settings.alphavantage_api_key
        super().__init__(**kwargs)

    def auth_params(self) -> Mapping[str, SecretStr | str]:
        return {"apikey": self._api_key}

    @property
    def is_configured(self) -> bool:
        """Whether a key has been supplied at all.

        Public because a *caller* may reasonably want to answer "is this feature available
        here" without provoking a failure — but reading it is not a substitute for handling
        the error, since a key can be present and still be rejected.
        """
        return bool(self._api_key.get_secret_value().strip())

    async def fetch_intraday(
        self,
        symbol: str,
        *,
        interval: IntradayInterval = IntradayInterval.FIVE_MINUTES,
        month: str | None = None,
    ) -> IntradaySeries:
        """Intraday OHLCV bars for one symbol.

        :param symbol: the vendor's ticker, taken as a primitive. Normalising it is the
            *service's* job (``CLAUDE.md`` §4) — a client that lower-cased its input would
            be applying an Anvex rule to a vendor's parameter.
        :param interval: bar width. Also selects the key the bars come back under.
        :param month: ``YYYY-MM`` for a specific historical month. Omitted, AlphaVantage
            returns the most recent trading days. *Which* months are worth fetching is
            ANV-22's rule, not this layer's.
        :returns: the parsed series. Never a ``Response``, never a raw dict.
        :raises ExternalServiceError: for every failure, including the two that arrive as a
            perfectly valid ``200``.
        """
        self._require_key()
        params: dict[str, str] = {
            "function": INTRADAY_FUNCTION,
            "symbol": symbol,
            "interval": str(interval),
            "outputsize": OUTPUT_SIZE,
        }
        if month is not None:
            params["month"] = month

        payload = await self.get_json(QUERY_PATH, params=params)
        return self._parse_intraday(payload, symbol=symbol, interval=interval)

    # ----- parsing -----------------------------------------------------------------
    #
    # The `try`s below are not the `try` a subclass is forbidden (`CLAUDE.md` §3): that one
    # wraps the *request*, and the base owns it. Turning the string `"abc"` into a Decimal
    # has no other spelling, and the alternative — pandas' `errors="coerce"` — is the bug
    # this module was written to remove.

    def _require_key(self) -> None:
        """Refuse before spending a round trip, when there is no key to spend it with.

        ``CLAUDE.md`` §3's rule for every client, and ``ALPHAVANTAGE_API_KEY`` defaults to a
        blank ``SecretStr`` — so "not configured" is the state of every fresh clone rather
        than an edge case. Deliberately **not** raised through :meth:`_error`, whose
        ``Failure`` members all describe how a *call* went wrong, and no call was made.

        **ANV-22 is why this exists.** Without it a keyless request really does leave the
        machine: AlphaVantage answers ``apikey=`` with a ``200`` carrying an ``Error
        Message``, which this module correctly classifies as ``client_error`` — and a
        scheduled ingest then spends a round trip per ticker per month, forever, reporting a
        misconfiguration as though the roster held bad symbols. ANV-19 and ANV-20 both had
        this guard; this module was the one that did not, and the fan-out is what made the
        omission expensive.
        """
        if not self.is_configured:
            raise ExternalServiceError(
                self.vendor,
                f"The upstream service '{self.vendor}' is not configured.",
                details={"reason": NOT_CONFIGURED, "setting": API_KEY_SETTING},
            )

    def _parse_intraday(
        self, payload: Any, *, symbol: str, interval: IntradayInterval
    ) -> IntradaySeries:
        """Validate a decoded body into :class:`IntradaySeries`, or raise.

        Order matters: the two 200-body failures are checked *before* the time series is
        looked for, because neither carries one and "missing time series" would be a much
        less useful diagnosis than "rate limited".
        """
        if not isinstance(payload, Mapping):
            raise self._error(Failure.MALFORMED)
        if any(key in payload for key in RATE_LIMIT_KEYS):
            # The whole point of this module's existence. A 200 that means 429.
            raise self._error(Failure.RATE_LIMITED)
        if ERROR_MESSAGE_KEY in payload:
            raise self._error(Failure.CLIENT_ERROR)

        series = payload.get(time_series_key(interval))
        if not isinstance(series, Mapping):
            raise self._error(Failure.MALFORMED)

        meta = payload.get(META_DATA_KEY)
        meta = meta if isinstance(meta, Mapping) else {}

        return IntradaySeries(
            symbol=str(meta.get(META_SYMBOL_KEY) or symbol),
            interval=interval,
            timezone=self._optional_text(meta.get(META_TIMEZONE_KEY)),
            # An empty series is an empty tuple, not a failure: a window with no trading in
            # it is an answer, and what to do about it is ANV-22's call.
            candles=tuple(self._parse_candle(timestamp, row) for timestamp, row in series.items()),
        )

    def _parse_candle(self, timestamp: Any, row: Any) -> IntradayCandle:
        """One ``"2024-01-31 19:55:00": {"1. open": …}`` pair, typed."""
        if not isinstance(row, Mapping):
            raise self._error(Failure.MALFORMED)

        moment = self._timestamp(timestamp)
        return IntradayCandle(
            # The split the old ETL did with `[d.date() for d in df['datetime']]`.
            date=moment.date(),
            time=moment.time(),
            open=self._decimal(row.get(OHLCV_KEYS["open"])),
            high=self._decimal(row.get(OHLCV_KEYS["high"])),
            low=self._decimal(row.get(OHLCV_KEYS["low"])),
            close=self._decimal(row.get(OHLCV_KEYS["close"])),
            volume=self._integer(row.get(OHLCV_KEYS["volume"])),
        )

    def _timestamp(self, raw: Any) -> dt.datetime:
        """The bar's index as a naive local datetime, before the date/time split."""
        if not isinstance(raw, str):
            raise self._error(Failure.MALFORMED)
        try:
            return dt.datetime.strptime(raw.strip(), TIMESTAMP_FORMAT)
        except ValueError as error:
            raise self._error(Failure.MALFORMED) from error

    def _decimal(self, raw: Any) -> Decimal:
        """A vendor price string as an exact :class:`~decimal.Decimal`.

        Via ``str`` rather than ``float`` on purpose: ``float("186.4200")`` is already the
        wrong number by the time it reaches ``Decimal``. Not rounded — see the module
        docstring.
        """
        if raw is None or isinstance(raw, bool):
            raise self._error(Failure.MALFORMED)
        try:
            value = Decimal(str(raw).strip())
        except (ArithmeticError, ValueError) as error:
            raise self._error(Failure.MALFORMED) from error
        if not value.is_finite():
            # `Decimal("NaN")` and `Decimal("Infinity")` parse without complaint, and
            # either would be a `NaN` in a NUMERIC column — the exact pandas failure mode
            # this module exists to avoid.
            raise self._error(Failure.MALFORMED)
        return value

    def _integer(self, raw: Any) -> int:
        """Volume. Numeric like a price, but a whole number or the vendor is wrong."""
        value = self._decimal(raw)
        if value != value.to_integral_value():
            raise self._error(Failure.MALFORMED)
        return int(value)

    @staticmethod
    def _optional_text(raw: Any) -> str | None:
        """Metadata is advisory: absent or blank is ``None``, never a failure."""
        if not isinstance(raw, str) or not raw.strip():
            return None
        return raw.strip()


__all__ = [
    "API_KEY_SETTING",
    "ERROR_MESSAGE_KEY",
    "INTRADAY_FUNCTION",
    "META_DATA_KEY",
    "META_SYMBOL_KEY",
    "META_TIMEZONE_KEY",
    "NOT_CONFIGURED",
    "OHLCV_KEYS",
    "OUTPUT_SIZE",
    "QUERY_PATH",
    "RATE_LIMIT_KEYS",
    "TIMESTAMP_FORMAT",
    "AlphaVantageClient",
    "IntradayCandle",
    "IntradayInterval",
    "IntradaySeries",
    "time_series_key",
]
