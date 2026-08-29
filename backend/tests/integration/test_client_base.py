"""Behaviour of ``app/clients/base.py`` against a ``respx``-mocked vendor.

``CLAUDE.md`` §6 puts client tests in ``tests/integration/`` because they exercise a real
``httpx.AsyncClient`` end to end — but this module asks for no ``db_*`` fixture, so it is
not marked ``db`` and **runs unchanged with Docker stopped**. Nothing here touches a live
vendor: ``mock_http`` answers every request and refuses to let one escape.

:class:`FakeVendorClient` below is the worked example of a subclass. It is what ANV-18's
``AlphaVantageClient`` will look like: two class attributes, one ``auth_params``, and one
method per vendor operation that returns a **typed model**. It contains no ``try``, no
status-code check, no retry loop and no logging, because those are exactly what a shared
base exists to stop three vendors getting subtly differently wrong.

The retry tests inject a recording sleeper, so a test that asserts a 400-millisecond backoff
finishes instantly and asserts the *decision* rather than the wall clock.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from pydantic import BaseModel, SecretStr
from structlog.testing import capture_logs

from app.clients.base import REDACTED, BaseHTTPClient, RetryPolicy
from app.domain.errors import AnvexError, ExternalServiceError

#: The credential that must never turn up in a log line or an exception.
API_KEY = "super-secret-key-9000"

BASE_URL = "https://vendor.example"
QUOTE_PATH = "/v1/quote"
QUOTE_URL = f"{BASE_URL}{QUOTE_PATH}"
PAYLOAD = {"symbol": "AAPL", "price": "1.23"}


class Quote(BaseModel):
    """The vendor's payload, typed. Deliberately not an ``app.schemas`` model."""

    symbol: str
    price: Decimal


class FakeVendorClient(BaseHTTPClient):
    """A subclass in the shape every real one takes."""

    vendor = "fakevendor"
    base_url = BASE_URL

    def __init__(self, *, api_key: SecretStr | None = None, **kwargs: Any) -> None:
        # The key stays a `SecretStr` on the instance; the base unwraps it per request.
        self._api_key = SecretStr(API_KEY) if api_key is None else api_key
        self.clients_built = 0
        super().__init__(**kwargs)

    def _new_client(self) -> httpx.AsyncClient:
        self.clients_built += 1
        return super()._new_client()

    def auth_params(self) -> Mapping[str, SecretStr | str]:
        return {"apikey": self._api_key}

    async def fetch_quote(self, symbol: str) -> Quote:
        payload = await self.get_json(
            QUOTE_PATH, params={"function": "GLOBAL_QUOTE", "symbol": symbol}
        )
        return Quote.model_validate(payload)


class HeaderAuthClient(BaseHTTPClient):
    """The other way vendors take a credential."""

    vendor = "headervendor"
    base_url = BASE_URL

    def auth_headers(self) -> Mapping[str, SecretStr | str]:
        return {"X-Api-Key": SecretStr(API_KEY)}


@pytest.fixture
def sleeps() -> list[float]:
    """Every delay the retry loop asked for, in order. Nothing actually waits."""
    return []


@pytest.fixture
async def client(sleeps: list[float]) -> AsyncIterator[FakeVendorClient]:
    async def record(delay: float) -> None:
        sleeps.append(delay)

    # `jitter=0.0` pins the backoff to its full length so the assertions can name numbers;
    # `TestRetryPolicy` in the unit tier covers the jittered range.
    vendor = FakeVendorClient(sleep=record, jitter=lambda: 0.0)
    try:
        yield vendor
    finally:
        await vendor.aclose()


# ---------------------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------------------


class TestASuccessfulCall:
    async def test_a_client_returns_typed_data_never_a_response(
        self, client: FakeVendorClient, mock_http: respx.MockRouter
    ) -> None:
        """``CLAUDE.md`` §3: no caller of a client should ever see an HTTP object."""
        route = mock_http.get(QUOTE_URL).respond(200, json=PAYLOAD)

        quote = await client.fetch_quote("AAPL")

        assert quote == Quote(symbol="AAPL", price=Decimal("1.23"))
        assert route.call_count == 1

    async def test_the_credential_is_actually_sent(
        self, client: FakeVendorClient, mock_http: respx.MockRouter
    ) -> None:
        """Redaction must not be achieved by never sending the key in the first place."""
        route = mock_http.get(QUOTE_URL).respond(200, json=PAYLOAD)

        await client.fetch_quote("AAPL")

        params = route.calls.last.request.url.params
        assert params["apikey"] == API_KEY
        assert params["function"] == "GLOBAL_QUOTE"
        assert params["symbol"] == "AAPL"

    async def test_an_auth_header_is_sent_as_a_header(self, mock_http: respx.MockRouter) -> None:
        route = mock_http.get(QUOTE_URL).respond(200, json=PAYLOAD)

        async with HeaderAuthClient() as vendor:
            await vendor.get_json(QUOTE_PATH)

        assert route.calls.last.request.headers["x-api-key"] == API_KEY

    async def test_anvex_identifies_itself_and_asks_for_json(
        self, client: FakeVendorClient, mock_http: respx.MockRouter
    ) -> None:
        route = mock_http.get(QUOTE_URL).respond(200, json=PAYLOAD)

        await client.fetch_quote("AAPL")

        headers = route.calls.last.request.headers
        assert headers["accept"] == "application/json"
        assert "anvex" in headers["user-agent"]


# ---------------------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------------------


class TestLifecycle:
    async def test_one_httpx_client_serves_every_call(
        self, client: FakeVendorClient, mock_http: respx.MockRouter
    ) -> None:
        """The reason the base owns the client: pooled connections, DNS and TLS."""
        mock_http.get(QUOTE_URL).respond(200, json=PAYLOAD)

        first = client.http
        await client.fetch_quote("AAPL")
        await client.fetch_quote("MSFT")

        assert client.clients_built == 1
        assert client.http is first

    async def test_the_pool_is_not_built_until_it_is_needed(self) -> None:
        """So constructing a client in a dependency factory costs nothing."""
        vendor = FakeVendorClient()

        assert vendor.clients_built == 0

        await vendor.aclose()

    async def test_closing_is_final(self, mock_http: respx.MockRouter) -> None:
        """Mirrors httpx's own refusal to reopen: silently reconnecting hides a leak."""
        mock_http.get(QUOTE_URL).respond(200, json=PAYLOAD)
        vendor = FakeVendorClient()
        await vendor.fetch_quote("AAPL")

        await vendor.aclose()

        assert vendor.is_closed
        with pytest.raises(RuntimeError, match="closed"):
            await vendor.fetch_quote("AAPL")

    async def test_closing_twice_is_harmless(self) -> None:
        vendor = FakeVendorClient()

        await vendor.aclose()
        await vendor.aclose()

    async def test_the_context_manager_closes_it(self, mock_http: respx.MockRouter) -> None:
        mock_http.get(QUOTE_URL).respond(200, json=PAYLOAD)

        async with FakeVendorClient() as vendor:
            await vendor.fetch_quote("AAPL")

        assert vendor.is_closed


# ---------------------------------------------------------------------------------------
# retry: what is tried again, and what is not
# ---------------------------------------------------------------------------------------


class TestRetryingWhatCanBeFixed:
    async def test_a_500_is_retried_and_the_second_answer_is_used(
        self, client: FakeVendorClient, mock_http: respx.MockRouter, sleeps: list[float]
    ) -> None:
        route = mock_http.get(QUOTE_URL).mock(
            side_effect=[httpx.Response(500), httpx.Response(200, json=PAYLOAD)]
        )

        quote = await client.fetch_quote("AAPL")

        assert quote.symbol == "AAPL"
        assert route.call_count == 2
        assert sleeps == [pytest.approx(0.2)]

    async def test_it_gives_up_after_the_attempt_budget(
        self, client: FakeVendorClient, mock_http: respx.MockRouter, sleeps: list[float]
    ) -> None:
        """Bounded: three tries, two waits, then a 502 — never an unbounded loop."""
        route = mock_http.get(QUOTE_URL).respond(503)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_quote("AAPL")

        assert route.call_count == 3
        assert sleeps == [pytest.approx(0.2), pytest.approx(0.4)]
        assert caught.value.details == {
            "service": "fakevendor",
            "reason": "server_error",
            "attempts": 3,
            "status_code": 503,
        }

    @pytest.mark.parametrize(
        "error",
        [
            httpx.ConnectTimeout("connect timed out"),
            httpx.ReadTimeout("read timed out"),
            httpx.ConnectError("connection refused"),
            httpx.ReadError("connection reset"),
        ],
        ids=["connect timeout", "read timeout", "connect error", "read error"],
    )
    async def test_a_timeout_or_network_error_becomes_an_external_service_error(
        self,
        client: FakeVendorClient,
        mock_http: respx.MockRouter,
        error: httpx.TransportError,
    ) -> None:
        """No ``httpx`` exception escapes this layer — otherwise every service imports httpx."""
        route = mock_http.get(QUOTE_URL).mock(side_effect=error)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_quote("AAPL")

        assert route.call_count == 3
        assert caught.value.details["reason"] == "transport_error"
        assert isinstance(caught.value.__cause__, httpx.TransportError)

    async def test_a_transport_error_that_clears_is_retried_successfully(
        self, client: FakeVendorClient, mock_http: respx.MockRouter
    ) -> None:
        route = mock_http.get(QUOTE_URL).mock(
            side_effect=[httpx.ConnectError("refused"), httpx.Response(200, json=PAYLOAD)]
        )

        assert (await client.fetch_quote("AAPL")).symbol == "AAPL"
        assert route.call_count == 2

    async def test_the_wall_clock_budget_stops_a_slow_upstream(
        self, mock_http: respx.MockRouter
    ) -> None:
        """The second bound. Attempts alone cannot stop three slow answers adding up."""
        route = mock_http.get(QUOTE_URL).respond(500)
        vendor = FakeVendorClient(retry=RetryPolicy(total_budget_seconds=0.001))

        with pytest.raises(ExternalServiceError):
            await vendor.fetch_quote("AAPL")

        assert route.call_count == 1
        await vendor.aclose()


class TestNotRetryingWhatCannotBeFixed:
    @pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 418, 422])
    async def test_a_4xx_is_never_retried(
        self,
        client: FakeVendorClient,
        mock_http: respx.MockRouter,
        sleeps: list[float],
        status: int,
    ) -> None:
        """The bug this base class exists to prevent.

        Retrying a 401 turns one permanent failure into three; the call count is the
        assertion that matters, because "it raised" would pass either way.
        """
        route = mock_http.get(QUOTE_URL).respond(status)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_quote("AAPL")

        assert route.call_count == 1
        assert sleeps == []
        assert caught.value.details["reason"] == "client_error"
        assert caught.value.details["status_code"] == status

    async def test_a_body_that_is_not_json_is_a_clean_external_service_error(
        self, client: FakeVendorClient, mock_http: respx.MockRouter, sleeps: list[float]
    ) -> None:
        """A ``JSONDecodeError`` escaping would be a 500 for something we already knew."""
        route = mock_http.get(QUOTE_URL).respond(200, text="<html>rate limit page</html>")

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_quote("AAPL")

        assert route.call_count == 1, "a vendor answering 200 with HTML is broken, not blipping"
        assert sleeps == []
        assert caught.value.details["reason"] == "malformed_response"
        assert isinstance(caught.value.__cause__, ValueError)

    async def test_an_empty_body_is_the_same_kind_of_failure(
        self, client: FakeVendorClient, mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(QUOTE_URL).respond(204)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_quote("AAPL")

        assert caught.value.details["reason"] == "malformed_response"

    async def test_a_protocol_error_is_not_treated_as_a_blip(
        self, client: FakeVendorClient, mock_http: respx.MockRouter, sleeps: list[float]
    ) -> None:
        """``httpx`` errors that are not transport errors — a redirect loop, a bad
        content-encoding — are real and repeatable, so they get one attempt like a 4xx."""
        route = mock_http.get(QUOTE_URL).mock(side_effect=httpx.TooManyRedirects("looping"))

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_quote("AAPL")

        assert route.call_count == 1
        assert sleeps == []
        assert caught.value.details["reason"] == "protocol_error"

    async def test_a_redirect_is_a_failure_rather_than_a_hop(
        self, client: FakeVendorClient, mock_http: respx.MockRouter
    ) -> None:
        """Following it would resend the credential-bearing URL to a host the vendor chose."""
        route = mock_http.get(QUOTE_URL).respond(302, headers={"Location": "https://elsewhere"})

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_quote("AAPL")

        assert route.call_count == 1
        assert caught.value.details["reason"] == "unexpected_redirect"


# ---------------------------------------------------------------------------------------
# 429 — a "not now", not a "never"
# ---------------------------------------------------------------------------------------


class TestRateLimiting:
    async def test_a_429_gets_one_retry_and_no_more(
        self, client: FakeVendorClient, mock_http: respx.MockRouter, sleeps: list[float]
    ) -> None:
        """Its own budget, shorter than the 5xx one: enough for a burst boundary, not enough
        to keep hammering a vendor that means it."""
        route = mock_http.get(QUOTE_URL).respond(429)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_quote("AAPL")

        assert route.call_count == 2
        assert len(sleeps) == 1
        assert caught.value.details["reason"] == "rate_limited"
        assert caught.value.details["attempts"] == 2

    async def test_a_429_that_clears_is_retried_successfully(
        self, client: FakeVendorClient, mock_http: respx.MockRouter
    ) -> None:
        route = mock_http.get(QUOTE_URL).mock(
            side_effect=[httpx.Response(429), httpx.Response(200, json=PAYLOAD)]
        )

        assert (await client.fetch_quote("AAPL")).symbol == "AAPL"
        assert route.call_count == 2

    async def test_a_short_retry_after_is_honoured_exactly(
        self, client: FakeVendorClient, mock_http: respx.MockRouter, sleeps: list[float]
    ) -> None:
        """The vendor knows its own window better than our backoff curve does."""
        mock_http.get(QUOTE_URL).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "1.5"}),
                httpx.Response(200, json=PAYLOAD),
            ]
        )

        await client.fetch_quote("AAPL")

        assert sleeps == [pytest.approx(1.5)]

    async def test_a_long_retry_after_fails_immediately_instead_of_waiting(
        self, client: FakeVendorClient, mock_http: respx.MockRouter, sleeps: list[float]
    ) -> None:
        """A minute is longer than any request path may be held open. Tell the caller when
        to come back and let go — a job reschedules, a user gets an answer."""
        route = mock_http.get(QUOTE_URL).respond(429, headers={"Retry-After": "60"})

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_quote("AAPL")

        assert route.call_count == 1
        assert sleeps == []
        assert caught.value.details["retry_after"] == pytest.approx(60.0)

    async def test_an_unparseable_retry_after_falls_back_to_the_backoff(
        self, client: FakeVendorClient, mock_http: respx.MockRouter, sleeps: list[float]
    ) -> None:
        """The ``HTTP-date`` form reaches us as "absent", which must not mean "wait forever"."""
        mock_http.get(QUOTE_URL).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}),
                httpx.Response(200, json=PAYLOAD),
            ]
        )

        await client.fetch_quote("AAPL")

        assert sleeps == [pytest.approx(0.2)]


# ---------------------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------------------


class TestCredentialsStaySecret:
    """The whole reason settings hold a ``SecretStr``.

    A key that is unwrapped into a URL and then logged has been leaked as thoroughly as one
    committed to git — more so, because log shipping fans it out. Every assertion below
    searches the *entire* captured record rather than one field, so a key smuggled into a
    new log key fails the suite too.
    """

    async def test_no_log_line_from_a_successful_call_contains_the_key(
        self, client: FakeVendorClient, mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(QUOTE_URL).respond(200, json=PAYLOAD)

        with capture_logs() as entries:
            await client.fetch_quote("AAPL")

        assert entries
        assert API_KEY not in json.dumps(entries, default=str)

    async def test_the_logged_url_is_redacted_but_still_useful(
        self, client: FakeVendorClient, mock_http: respx.MockRouter
    ) -> None:
        """Blanking the whole query would be safe and worthless. These stay legible."""
        mock_http.get(QUOTE_URL).respond(200, json=PAYLOAD)

        with capture_logs() as entries:
            await client.fetch_quote("AAPL")

        logged = json.dumps(entries, default=str)
        assert f"apikey={REDACTED}" in logged
        assert "function=GLOBAL_QUOTE" in logged
        assert "symbol=AAPL" in logged

    @pytest.mark.parametrize(
        "outcome",
        ["server_error", "client_error", "rate_limited", "transport_error", "malformed"],
    )
    async def test_no_failure_path_logs_the_key_either(
        self, client: FakeVendorClient, mock_http: respx.MockRouter, outcome: str
    ) -> None:
        """The failure paths log the most, so they are where a leak would actually happen."""
        route = mock_http.get(QUOTE_URL)
        if outcome == "server_error":
            route.respond(500)
        elif outcome == "client_error":
            route.respond(401)
        elif outcome == "rate_limited":
            route.respond(429, headers={"Retry-After": "1"})
        elif outcome == "malformed":
            route.respond(200, text="not json")
        else:
            route.mock(side_effect=httpx.ConnectError(f"failed talking to {QUOTE_URL}"))

        with capture_logs() as entries, pytest.raises(ExternalServiceError):
            await client.fetch_quote("AAPL")

        assert entries
        assert API_KEY not in json.dumps(entries, default=str)

    async def test_a_header_credential_is_never_logged(self, mock_http: respx.MockRouter) -> None:
        """Headers are not logged at all — there is no redaction to forget."""
        mock_http.get(QUOTE_URL).respond(200, json=PAYLOAD)

        with capture_logs() as entries:
            async with HeaderAuthClient() as vendor:
                await vendor.get_json(QUOTE_PATH)

        assert API_KEY not in json.dumps(entries, default=str)

    async def test_a_librarys_own_message_is_scrubbed_before_it_is_logged(
        self, client: FakeVendorClient, mock_http: respx.MockRouter
    ) -> None:
        """Belt to the braces: we do not compose a transport error's text, so we clean it."""
        mock_http.get(QUOTE_URL).mock(
            side_effect=httpx.ConnectError(f"failed to reach ?apikey={API_KEY}")
        )

        with capture_logs() as entries, pytest.raises(ExternalServiceError):
            await client.fetch_quote("AAPL")

        logged = json.dumps(entries, default=str)
        assert API_KEY not in logged
        assert REDACTED in logged

    @pytest.mark.parametrize("status", [401, 429, 500])
    async def test_the_exception_carries_nothing_derived_from_the_key(
        self, client: FakeVendorClient, mock_http: respx.MockRouter, status: int
    ) -> None:
        """``details`` is serialised straight to an API consumer — see ``CLAUDE.md`` §4."""
        mock_http.get(QUOTE_URL).respond(status)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_quote("AAPL")

        error = caught.value
        assert API_KEY not in str(error)
        assert API_KEY not in repr(error)
        assert API_KEY not in json.dumps(error.details, default=str)

    async def test_the_error_body_never_forwards_the_vendors_own_output(
        self, client: FakeVendorClient, mock_http: respx.MockRouter
    ) -> None:
        """The upstream's body is logged, not forwarded: it is not ours to publish."""
        mock_http.get(QUOTE_URL).respond(500, json={"secretInternalTrace": "stack frame"})

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_quote("AAPL")

        assert "secretInternalTrace" not in json.dumps(caught.value.details)
        assert "secretInternalTrace" not in str(caught.value)
        assert set(caught.value.details) == {"service", "reason", "attempts", "status_code"}


# ---------------------------------------------------------------------------------------
# the error contract
# ---------------------------------------------------------------------------------------


class TestTheErrorContract:
    async def test_every_failure_is_the_error_the_middleware_maps_to_502(
        self, client: FakeVendorClient, mock_http: respx.MockRouter
    ) -> None:
        """``ExternalServiceError`` had no producer before ANV-17. Now it has one."""
        mock_http.get(QUOTE_URL).respond(500)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_quote("AAPL")

        assert isinstance(caught.value, AnvexError)
        assert caught.value.code == "external_service_error"
        assert caught.value.details["service"] == "fakevendor"

    async def test_the_message_names_the_vendor_and_says_what_happened(
        self, client: FakeVendorClient, mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(QUOTE_URL).respond(429)

        with pytest.raises(ExternalServiceError) as caught:
            await client.fetch_quote("AAPL")

        assert "fakevendor" in caught.value.message
        assert "rate limiting" in caught.value.message
