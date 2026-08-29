"""Pydantic request/response contracts (``CLAUDE.md`` §3).

The resource schemas arrive with ANV-8; what is here now is framework-level — the shared
error envelope every endpoint can fail with, and the health probe bodies.
"""

from app.schemas.errors import ErrorBody, ErrorResponse
from app.schemas.health import HealthOut, ReadinessOut

__all__ = ["ErrorBody", "ErrorResponse", "HealthOut", "ReadinessOut"]
