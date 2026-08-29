"""The ``mock_http`` fixture — the pattern every ``app/clients/`` test will follow.

Two things are being demonstrated:

1. **A vendor call is answered from the fixture, never from the network.** ``CLAUDE.md`` §6
   forbids a test touching a live vendor API, and an un-mocked request raises rather than
   escaping the machine.
2. **This module has no database dependency, so it does not skip.** It lives in
   ``tests/integration/`` next to the database tests and still runs with Docker stopped —
   which is the point of making the skip fixture-driven rather than directory-driven.

ANV-8 onward: replace ``httpx.AsyncClient`` below with the real client class. Nothing else
about the shape changes.
"""

from __future__ import annotations

import httpx
import pytest
import respx

VENDOR_URL = "https://vendor.example/v1/quote"


async def test_a_mocked_route_answers_instead_of_the_network(
    mock_http: respx.MockRouter,
) -> None:
    route = mock_http.get(VENDOR_URL).respond(200, json={"symbol": "AAPL", "price": "1.23"})

    async with httpx.AsyncClient() as client:
        response = await client.get(VENDOR_URL)

    assert response.status_code == 200
    assert response.json()["symbol"] == "AAPL"
    # Asserting on the route is how a test pins "this call happened", which is why the
    # fixture leaves `assert_all_called` off.
    assert route.call_count == 1


async def test_query_parameters_can_be_matched(mock_http: respx.MockRouter) -> None:
    """Clients differ mainly in how they build a request, so match on the request."""
    mock_http.get(VENDOR_URL, params={"symbol": "MSFT"}).respond(200, json={"price": "2.34"})

    async with httpx.AsyncClient() as client:
        response = await client.get(VENDOR_URL, params={"symbol": "MSFT"})

    assert response.json() == {"price": "2.34"}


async def test_an_upstream_failure_can_be_simulated(mock_http: respx.MockRouter) -> None:
    """Every client needs a test for the 5xx path that becomes `ExternalServiceError`."""
    mock_http.get(VENDOR_URL).respond(503)

    async with httpx.AsyncClient() as client:
        assert (await client.get(VENDOR_URL)).status_code == 503


async def test_a_transport_error_can_be_simulated(mock_http: respx.MockRouter) -> None:
    mock_http.get(VENDOR_URL).mock(side_effect=httpx.ConnectTimeout("upstream is gone"))

    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.ConnectTimeout):
            await client.get(VENDOR_URL)


async def test_an_unmocked_call_is_refused_rather_than_escaping(
    mock_http: respx.MockRouter,
) -> None:
    """The guarantee that matters: nothing reaches the real internet from a test."""
    async with httpx.AsyncClient() as client:
        with pytest.raises(respx.models.AllMockedAssertionError):
            await client.get("https://not-registered.example/anything")
