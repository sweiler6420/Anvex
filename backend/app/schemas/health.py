"""Response contracts for the liveness and readiness probes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealthOut(BaseModel):
    """``GET /health`` — the process is up. Says nothing about its dependencies."""

    status: Literal["ok"] = "ok"


class ReadinessOut(BaseModel):
    """``GET /health/ready`` — the process can serve traffic.

    Only returned with a 200; an unready service answers 503 with the standard error
    body, so there is no ``"status": "degraded"`` variant to parse.
    """

    status: Literal["ok"] = "ok"
    database: Literal["ok"] = Field(default="ok", description="Result of a `SELECT 1`.")


__all__ = ["HealthOut", "ReadinessOut"]
