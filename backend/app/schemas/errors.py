"""The one error body every non-2xx Anvex response uses.

Defined as pydantic models rather than a hand-built ``dict`` for two reasons: the
middleware cannot drift from the documented shape, and FastAPI can advertise it in
OpenAPI so the frontend generates against it.

```json
{
  "error": {
    "code": "not_found",
    "message": "stock 'AAPL' was not found.",
    "details": {"resource": "stock", "identifier": "AAPL"},
    "request_id": "0f0f2f2e-0b1c-4a0b-9a8e-1d2c3b4a5f60"
  }
}
```

All four keys are **always** present. ``details`` is ``{}`` rather than ``null`` when
there is nothing to add, so a consumer can index it unconditionally; ``request_id`` is
``null`` only for a response produced outside the request middleware, which should not
happen in practice.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    """The contents of the ``error`` envelope."""

    code: str = Field(
        description="Stable machine-readable slug. Branch on this, never on `message`.",
        examples=["not_found"],
    )
    message: str = Field(
        description="Human-readable sentence, safe to display to an API consumer.",
        examples=["stock 'AAPL' was not found."],
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured context. `{}` when there is none — never null.",
    )
    request_id: str | None = Field(
        default=None,
        description="Correlates with the `X-Request-ID` response header and the server logs.",
    )


class ErrorResponse(BaseModel):
    """Top-level body of every error response.

    The single-key envelope exists so a success payload can never be mistaken for an
    error one: clients test for the presence of ``error``.
    """

    error: ErrorBody


__all__ = ["ErrorBody", "ErrorResponse"]
