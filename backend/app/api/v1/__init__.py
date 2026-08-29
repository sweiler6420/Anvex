"""Version 1 of the HTTP API.

The single aggregation point for every ``/v1`` router. The ``prefix`` lives here and
nowhere else (``CLAUDE.md`` §4) — a path decorator must never spell out ``/v1``.

Adding a resource from ANV-11 onward is two lines::

    from app.api.v1 import auth
    router.include_router(auth.router)

with the resource module owning its own ``prefix="/auth"`` and ``tags``.
"""

from fastapi import APIRouter

from app.api.v1 import auth, users

router = APIRouter(prefix="/v1")

# Resource routers are included here as they land, in the order they should read in the
# generated docs. Each module owns its own `prefix` and `tags`.
router.include_router(auth.router)
router.include_router(users.router)

__all__ = ["router"]
