"""Middleware registration — the one place the stack order is decided."""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from app.middleware.logging import AccessLogMiddleware
from app.middleware.request_id import REQUEST_ID_HEADER, RequestIDMiddleware
from app.settings import Settings, get_settings


def install_middleware(app: FastAPI, settings: Settings | None = None) -> None:
    """Attach the cross-cutting middleware stack to ``app``.

    Registration order is reversed by Starlette (last added is outermost), so this reads
    bottom-up: CORS innermost, then the access log, then the request id on the outside.
    """
    settings = settings or get_settings()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Without this the browser hides `X-Request-ID` from the frontend, which is the
        # one header a user-facing error dialog wants to quote.
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIDMiddleware)


__all__ = ["install_middleware"]
