"""The ``/v1/users`` service factory — wiring, and nothing else.

One seam per resource (``CLAUDE.md`` §3): :func:`get_user_service` resolves a session and a
:class:`~app.settings.Settings` out of the dependency graph, constructs the service and
returns it. Nothing is decided here, which is precisely what makes it the single
dependency a route contract test overrides to swap the whole service for one sitting on an
in-memory repo.

There is deliberately **no** ``get_user_by_id`` dependency and no "load the account this
path names" resolver. Whether a caller may see the account ``{user_id}`` names is an
authorization rule, and authorization is logic: it lives in
:meth:`~app.services.user.UserService.get_user` where a Celery task or a WebSocket handler
can reach it too, and where it is unit-testable without FastAPI. The account behind the
bearer token arrives through ``CurrentUser`` from ``app/deps/auth.py``; this module does
not duplicate it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.session import get_session
from app.deps.settings import Settings, get_settings_dep
from app.services.user import UserService


def get_user_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> UserService:
    """Build a :class:`~app.services.user.UserService` for this request.

    The repo is left to its keyword default — repos are stateless singletons, so the only
    thing that genuinely varies per request is the session.
    """
    return UserService(session, settings)


#: The annotation a ``/v1/users`` handler uses, so a route signature stays one parameter.
UserServiceDep = Annotated[UserService, Depends(get_user_service)]

__all__ = ["UserServiceDep", "get_user_service"]
