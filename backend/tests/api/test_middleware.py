"""Contract tests for the cross-cutting middleware stack.

These assert the two things every later ticket builds on: the ``X-Request-ID`` behaviour
and the **exact** error body shape. If one of these has to change, the change is a
breaking API change and belongs in ``CLAUDE.md`` first.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import BaseModel

from app.domain.errors import (
    AnvexError,
    ConflictError,
    ExternalServiceError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from app.middleware.request_id import REQUEST_ID_HEADER
from tests.conftest import ALLOWED_ORIGIN

#: Every key the envelope promises, always present.
ERROR_BODY_KEYS = {"code", "message", "details", "request_id"}

SECRET = "postgresql://anvex:hunter2@db:5432/anvex"


class _Payload(BaseModel):
    ticker: str
    quantity: int


@pytest.fixture(autouse=True)
def probe_routes(app: FastAPI) -> None:
    """Attach routes that raise each error kind on demand."""

    @app.get("/probe/ok")
    async def ok() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/probe/not-found")
    async def not_found() -> None:
        raise NotFoundError("stock", "AAPL")

    @app.get("/probe/conflict")
    async def conflict() -> None:
        raise ConflictError("user", "a@b.com")

    @app.get("/probe/validation")
    async def validation() -> None:
        raise ValidationError("End date precedes start date.", field="end_date")

    @app.get("/probe/unauthorized")
    async def unauthorized() -> None:
        raise UnauthorizedError()

    @app.get("/probe/forbidden")
    async def forbidden() -> None:
        raise ForbiddenError()

    @app.get("/probe/external")
    async def external() -> None:
        raise ExternalServiceError("alphavantage")

    @app.get("/probe/base-error")
    async def base_error() -> None:
        raise AnvexError("Something we named but did not classify.")

    @app.get("/probe/boom")
    async def boom() -> None:
        raise RuntimeError(f"connection string {SECRET} exploded")

    @app.post("/probe/body")
    async def body(payload: _Payload) -> _Payload:
        return payload


class TestRequestId:
    async def test_an_inbound_request_id_is_echoed(self, client: AsyncClient) -> None:
        incoming = "trace-abc-123"
        response = await client.get("/probe/ok", headers={REQUEST_ID_HEADER: incoming})
        assert response.headers[REQUEST_ID_HEADER] == incoming

    async def test_one_is_generated_when_absent(self, client: AsyncClient) -> None:
        response = await client.get("/probe/ok")
        generated = response.headers[REQUEST_ID_HEADER]
        assert uuid.UUID(generated).version == 4

    async def test_each_request_gets_its_own_id(self, client: AsyncClient) -> None:
        first = await client.get("/probe/ok")
        second = await client.get("/probe/ok")
        assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]

    @pytest.mark.parametrize(
        "hostile",
        ["x" * 200, "id with spaces", "id;<script>alert(1)</script>", ""],
        ids=["too-long", "spaces", "markup", "empty"],
    )
    async def test_an_unsafe_inbound_id_is_replaced_not_echoed(
        self, client: AsyncClient, hostile: str
    ) -> None:
        """The header is reflected back to the client, so it is untrusted input."""
        response = await client.get("/probe/ok", headers={REQUEST_ID_HEADER: hostile})
        echoed = response.headers[REQUEST_ID_HEADER]
        assert echoed != hostile
        assert uuid.UUID(echoed).version == 4

    async def test_the_header_appears_exactly_once(self, client: AsyncClient) -> None:
        response = await client.get("/probe/ok", headers={REQUEST_ID_HEADER: "abc"})
        assert response.headers.get_list(REQUEST_ID_HEADER) == ["abc"]

    async def test_the_error_body_carries_the_same_id_as_the_header(
        self, client: AsyncClient
    ) -> None:
        """This correlation is the point: a user quotes the id, support finds the log line."""
        response = await client.get("/probe/not-found", headers={REQUEST_ID_HEADER: "corr-1"})
        assert response.headers[REQUEST_ID_HEADER] == "corr-1"
        assert response.json()["error"]["request_id"] == "corr-1"

    async def test_health_endpoints_are_tagged_too(self, client: AsyncClient) -> None:
        response = await client.get("/health")
        assert REQUEST_ID_HEADER in response.headers


class TestDomainErrorMapping:
    @pytest.mark.parametrize(
        ("path", "expected_status", "expected_code"),
        [
            ("/probe/not-found", 404, "not_found"),
            ("/probe/conflict", 409, "conflict"),
            ("/probe/validation", 422, "validation_error"),
            ("/probe/unauthorized", 401, "unauthorized"),
            ("/probe/forbidden", 403, "forbidden"),
            ("/probe/external", 502, "external_service_error"),
            ("/probe/base-error", 500, "internal_error"),
        ],
    )
    async def test_status_and_code(
        self, client: AsyncClient, path: str, expected_status: int, expected_code: str
    ) -> None:
        response = await client.get(path)
        assert response.status_code == expected_status
        assert response.json()["error"]["code"] == expected_code

    @pytest.mark.parametrize(
        "path",
        [
            "/probe/not-found",
            "/probe/conflict",
            "/probe/validation",
            "/probe/unauthorized",
            "/probe/forbidden",
            "/probe/external",
            "/probe/base-error",
            "/probe/boom",
        ],
    )
    async def test_the_body_shape_is_identical_for_every_error(
        self, client: AsyncClient, path: str
    ) -> None:
        payload = (await client.get(path)).json()
        assert set(payload) == {"error"}
        error = payload["error"]
        assert set(error) == ERROR_BODY_KEYS
        assert isinstance(error["code"], str) and error["code"]
        assert isinstance(error["message"], str) and error["message"]
        assert isinstance(error["details"], dict)
        assert isinstance(error["request_id"], str)

    async def test_details_carry_the_structured_context(self, client: AsyncClient) -> None:
        error = (await client.get("/probe/not-found")).json()["error"]
        assert error["message"] == "stock 'AAPL' was not found."
        assert error["details"] == {"resource": "stock", "identifier": "AAPL"}

    async def test_a_domain_validation_error_reports_its_field(self, client: AsyncClient) -> None:
        error = (await client.get("/probe/validation")).json()["error"]
        assert error["details"] == {"field": "end_date"}


class TestFrameworkErrorsUseTheSameShape:
    async def test_unknown_route_404(self, client: AsyncClient) -> None:
        payload = (await client.get("/nope")).json()
        assert set(payload["error"]) == ERROR_BODY_KEYS
        assert payload["error"]["code"] == "not_found"

    async def test_wrong_method_405(self, client: AsyncClient) -> None:
        response = await client.post("/probe/ok")
        assert response.status_code == 405
        assert response.json()["error"]["code"] == "method_not_allowed"

    async def test_request_validation_422_keeps_the_field_errors(self, client: AsyncClient) -> None:
        response = await client.post("/probe/body", json={"ticker": "AAPL", "quantity": "many"})
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "validation_error"
        assert error["message"] == "Request validation failed."
        assert error["details"]["errors"][0]["loc"] == ["body", "quantity"]


class TestUnhandledExceptions:
    async def test_a_crash_becomes_a_clean_500(self, client: AsyncClient) -> None:
        response = await client.get("/probe/boom")
        assert response.status_code == 500
        error = response.json()["error"]
        assert error["code"] == "internal_error"
        assert error["message"] == "An unexpected error occurred."
        assert error["details"] == {}

    async def test_the_500_body_leaks_nothing_internal(self, client: AsyncClient) -> None:
        body = (await client.get("/probe/boom")).text
        for leak in (SECRET, "hunter2", "RuntimeError", "Traceback", "test_middleware"):
            assert leak not in body

    async def test_a_crash_is_still_correlated(self, client: AsyncClient) -> None:
        response = await client.get("/probe/boom", headers={REQUEST_ID_HEADER: "crash-1"})
        assert response.headers[REQUEST_ID_HEADER] == "crash-1"
        assert response.json()["error"]["request_id"] == "crash-1"

    async def test_the_traceback_is_logged_server_side(
        self, client: AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("ERROR"):
            await client.get("/probe/boom", headers={REQUEST_ID_HEADER: "crash-2"})
        logged = "\n".join(record.getMessage() for record in caplog.records)
        assert "request.unhandled_error" in logged
        assert "crash-2" in logged
        assert any(record.exc_info for record in caplog.records)


class TestCors:
    async def test_an_allowed_origin_gets_the_cors_headers(self, client: AsyncClient) -> None:
        response = await client.get("/probe/ok", headers={"Origin": ALLOWED_ORIGIN})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
        assert response.headers["access-control-allow-credentials"] == "true"

    async def test_the_request_id_header_is_exposed_to_the_browser(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/probe/ok", headers={"Origin": ALLOWED_ORIGIN})
        assert REQUEST_ID_HEADER in response.headers["access-control-expose-headers"].lower()

    async def test_a_preflight_is_answered_and_still_correlated(self, client: AsyncClient) -> None:
        response = await client.options(
            "/probe/ok",
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
        assert REQUEST_ID_HEADER in response.headers

    async def test_a_disallowed_origin_gets_no_allow_origin_header(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/probe/ok", headers={"Origin": "http://evil.example"})
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers
