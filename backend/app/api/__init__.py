"""The HTTP surface (``CLAUDE.md`` §3).

Two routers are mounted by ``create_app()``: the unversioned operational endpoints
(``health``) and the versioned application API (``v1``).
"""

from app.api import health
from app.api.v1 import router as v1_router

health_router = health.router

__all__ = ["health_router", "v1_router"]
