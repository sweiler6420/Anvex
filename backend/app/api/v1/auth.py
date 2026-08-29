"""``/v1/auth`` — sign in, rotate a token pair, ask for account recovery.

The first resource router in the repo, and therefore the template (``CLAUDE.md`` §3): each
handler accepts a validated request, calls **exactly one** service method, and returns a
schema. There is no ``try``, no ``if``, no ``HTTPException`` and no session — a service
raises a domain error and ``app/middleware/errors.py`` turns it into the one envelope every
non-2xx response uses. Everything a reader might want to know about *why* these routes
behave as they do is in ``app/services/auth.py``; a handler is not the place for it.

The ``prefix`` here is ``/auth`` only. ``/v1`` belongs to the aggregating router in
``app/api/v1/__init__.py`` and is never spelled in a path decorator (``CLAUDE.md`` §4).

Three deliberate differences from the API this replaces:

* ``POST /refresh`` takes a **JSON body**. The old endpoint took the refresh token as a
  query parameter, which wrote a long-lived credential into every proxy log, access log and
  browser history entry between the client and us.
* ``POST /refresh`` **rejects an access token** with 401 ``wrong_token_type``. The old one
  accepted it and returned a fresh long-lived pair, so a leaked short-lived token could be
  renewed forever. That is the fix this whole epic exists for.
* ``POST /recovery`` answers **the same thing for every username**. The old one answered
  404 ``"User not found with username: <x>"``, i.e. a free enumeration oracle.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm

from app.deps.auth import AuthServiceDep
from app.schemas.auth import RecoveryAccepted, RecoveryRequest, RefreshRequest, TokenPair
from app.schemas.errors import ErrorResponse

router = APIRouter(prefix="/auth", tags=["auth"])

#: Documented on both token endpoints so a generated client knows a 401 here is normal
#: traffic rather than an outage, and which ``code`` values to branch on.
UNAUTHORIZED_RESPONSE = {
    status.HTTP_401_UNAUTHORIZED: {
        "model": ErrorResponse,
        "description": (
            "`unauthorized` (bad credentials or a deleted account), `invalid_token`, "
            "`token_expired`, or `wrong_token_type`."
        ),
    }
}


@router.post(
    "/login",
    response_model=TokenPair,
    summary="Exchange credentials for a token pair",
    responses=UNAUTHORIZED_RESPONSE,
)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    service: AuthServiceDep,
) -> TokenPair:
    """Sign in with an email address **or** a username, plus a password.

    Form-encoded rather than JSON because ``CLAUDE.md`` §4 fixes the OAuth2 password flow;
    modelling this body as JSON would break Swagger's Authorize button and every standard
    client. ``form.username`` is the identifier: OAuth2 names the field, we accept either.
    """
    return await service.login(identifier=form.username, password=form.password)


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Rotate a refresh token into a new pair",
    responses=UNAUTHORIZED_RESPONSE,
)
async def refresh(body: RefreshRequest, service: AuthServiceDep) -> TokenPair:
    """Exchange a valid **refresh** token for a brand-new pair.

    An access token presented here is a 401 with code ``wrong_token_type``.
    """
    return await service.refresh(refresh_token=body.refresh_token)


@router.post(
    "/recovery",
    response_model=RecoveryAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request a password reset",
)
async def recovery(body: RecoveryRequest, service: AuthServiceDep) -> RecoveryAccepted:
    """Record a password-reset request. Always 202, whether or not the account exists.

    202 rather than 200 because that is what this genuinely is: the request is accepted for
    processing and nothing has been delivered yet. It has no 401 or 404 response documented
    because it has none to give — a status code that varied by account would leak exactly
    what the identical body is there to hide.
    """
    return await service.recovery(username=body.username)


__all__ = ["router"]
