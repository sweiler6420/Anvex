"""Request-ID correlation.

Every request gets an id: the inbound ``X-Request-ID`` when the caller supplies one (so a
trace started at the frontend or a load balancer survives the hop), otherwise a fresh
UUID4. The id is

* bound into the structlog context, so **every** log line emitted while handling the
  request carries it without anyone passing it around;
* stashed on ``request.state.request_id`` for exception handlers and endpoints;
* echoed back on the response, so a user reporting a failure can quote an id that finds
  the exact server-side traceback.

Written as raw ASGI rather than ``BaseHTTPMiddleware``: the latter runs the handler in a
separate task, which breaks ``contextvars`` propagation back out to the response phase and
adds a queue between us and the app for no benefit here.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import MutableMapping
from typing import Any

import structlog
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: Canonical header name, lower-cased (ASGI headers are always lower-case on the wire).
REQUEST_ID_HEADER = "x-request-id"

#: An inbound id is echoed into a response header, so it is untrusted input. Only a short
#: token of safe characters is accepted — anything else (CR/LF for header injection, a
#: megabyte of junk, control characters) is discarded and replaced with a generated id.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:@+/=-]{1,128}$")


def new_request_id() -> str:
    """Generate a fresh request id."""
    return str(uuid.uuid4())


def sanitize_request_id(value: str | None) -> str | None:
    """Return ``value`` if it is a safe id to echo, else ``None``."""
    if value is None:
        return None
    candidate = value.strip()
    return candidate if _SAFE_REQUEST_ID.match(candidate) else None


def get_request_id(scope: MutableMapping[str, Any]) -> str | None:
    """Read the id this middleware assigned to ``scope``, if it ran."""
    state = scope.get("state")
    if isinstance(state, MutableMapping):
        value = state.get("request_id")
        if isinstance(value, str):
            return value
    return None


class RequestIDMiddleware:
    """Assign, bind and echo the per-request correlation id."""

    def __init__(self, app: ASGIApp, header_name: str = REQUEST_ID_HEADER) -> None:
        self.app = app
        self.header_name = header_name.lower()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = Headers(scope=scope).get(self.header_name)
        request_id = sanitize_request_id(inbound) or new_request_id()
        scope.setdefault("state", {})["request_id"] = request_id

        encoded = (self.header_name.encode("latin-1"), request_id.encode("latin-1"))

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    header
                    for header in message.setdefault("headers", [])
                    if header[0].lower() != encoded[0]
                ]
                headers.append(encoded)
                message["headers"] = headers
            await send(message)

        # `bind_contextvars` is per-task; `clear_contextvars` at the end keeps a pooled
        # worker task from leaking one request's id into the next.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            structlog.contextvars.clear_contextvars()


__all__ = [
    "REQUEST_ID_HEADER",
    "RequestIDMiddleware",
    "get_request_id",
    "new_request_id",
    "sanitize_request_id",
]
