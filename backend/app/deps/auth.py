"""Authentication dependencies: the bearer scheme, the service factory, the current user.

Everything here is **wiring** (``CLAUDE.md`` §3). Not one of these functions decides
anything: they pull a session and a :class:`~app.settings.Settings` out of the dependency
graph, hand them to :class:`~app.services.auth.AuthService`, and pass its answer on. The
decode-and-re-read that turns a bearer header into a :class:`~app.models.User` lives in the
service, so a Celery task or a WebSocket handler can do it without FastAPI.

Two things are worth reading twice.

**``get_current_user`` calls** :func:`~app.services.auth.AuthService.authenticate`, **which
calls** :func:`~app.domain.auth.decode_access_token` — never ``decode_token`` directly. The
type-pinning decoders exist precisely so that no caller has to remember to pass
``expected_type``, and a dependency reaching past them to the generic decoder would put the
old ``/v1/refresh`` hole back in the one place every protected route depends on.

**The scheme's ``auto_error`` stays ``True``.** An anonymous caller therefore gets
Starlette's 401 with ``WWW-Authenticate: Bearer``, re-shaped into the standard Anvex
envelope by ``app/middleware/errors.py`` with code ``unauthorized``. A caller who *did*
present something gets the more specific ``invalid_token`` / ``token_expired`` /
``wrong_token_type`` from the domain — which is the split a client actually needs: no
credentials means "log in", an expired one means "refresh".
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.session import get_session
from app.deps.settings import Settings, get_settings_dep
from app.models import User
from app.services.auth import AuthService

#: Where Swagger's **Authorize** button posts credentials. Relative (no leading slash), as
#: OpenAPI expects, and it must name the real login route — ``app/api/v1/__init__.py``
#: mounts the auth router under the ``/v1`` prefix at ``/auth/login``. If the route ever
#: moves, this moves with it or the docs page silently stops being able to sign in.
TOKEN_URL = "v1/auth/login"

#: Extracts ``Authorization: Bearer <token>``. ``auto_error=True``: a request with no
#: credentials at all is a 401 before any of our code runs.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=TOKEN_URL)


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> AuthService:
    """Build an :class:`~app.services.auth.AuthService` for this request.

    The service-factory pattern for the whole repo: resolve the collaborators from the
    dependency graph, construct, return. No configuration is read here and no logic runs —
    which is also what lets a test override this single dependency to inject a stub service
    and contract-test a route without a database.
    """
    return AuthService(session, settings)


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> User:
    """The account behind the bearer token, or a 401.

    :raises app.domain.auth.TokenError: malformed, expired, or a refresh token presented
        where an access token belongs.
    :raises app.domain.errors.UnauthorizedError: the account has since been deleted.
    """
    return await service.authenticate(token)


#: The annotation every protected route uses: ``user: CurrentUser``. Spelled once so a
#: handler never repeats ``Annotated[User, Depends(get_current_user)]`` — and so adding an
#: extra check later (a disabled-account guard, say) is one edit rather than dozens.
CurrentUser = Annotated[User, Depends(get_current_user)]

#: Likewise for handlers that want the service itself.
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]

__all__ = [
    "TOKEN_URL",
    "AuthServiceDep",
    "CurrentUser",
    "get_auth_service",
    "get_current_user",
    "oauth2_scheme",
]
