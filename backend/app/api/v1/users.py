"""``/v1/users`` — register an account, read your own, resolve one by id.

Written to the handler shape ``app/api/v1/auth.py`` established (``CLAUDE.md`` §3): accept
a validated request, call **one** service method, return a schema. No ``try``, no ``if``,
no session, no ``HTTPException`` — a service raises a domain error and
``app/middleware/errors.py`` renders the one envelope every non-2xx uses. Why the routes
behave as they do is documented in ``app/services/user.py``; a handler is not the place
for it.

**These are the first protected routes in the API.** ``user: CurrentUser`` is the whole of
the guard, and its presence is what makes ``securitySchemes`` appear in the generated
OpenAPI document and Swagger's *Authorize* button start working.

Three defects are fixed here relative to the router this replaces
(``AverageInvestorApi/api/routers/user.py``):

* Its detail route was declared ``@router.get('{id}')`` with **no leading slash**, so it
  mounted as ``/v1/users{id}`` and never matched the URL anybody thought it did.
* That handler declared the path parameter as ``id`` in the decorator but read ``user_id``
  in the signature, so the 404 message interpolated the builtin ``id`` function.
* There was **no ``/me``**, which is the one thing a signed-in frontend actually needs; it
  had to keep a decoded token's claims and hope they were current.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, status

from app.deps.auth import CurrentUser
from app.deps.user import UserServiceDep
from app.schemas.errors import ErrorResponse
from app.schemas.user import UserCreate, UserOut

router = APIRouter(prefix="/users", tags=["users"])

#: Documented on both protected routes so a generated client knows a 401 here is ordinary
#: traffic and which ``code`` to branch on — ``token_expired`` means refresh, the rest mean
#: sign in again.
UNAUTHORIZED_RESPONSE = {
    status.HTTP_401_UNAUTHORIZED: {
        "model": ErrorResponse,
        "description": (
            "`unauthorized` (no credentials, or a deleted account), `invalid_token`, "
            "`token_expired`, or `wrong_token_type`."
        ),
    }
}


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register an account",
    responses={
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": (
                "`conflict` — the email address or username is taken. `details.field` "
                "names which one, so a form can highlight it."
            ),
        }
    },
)
async def register_user(body: UserCreate, service: UserServiceDep) -> UserOut:
    """Create an account from an email address, a username and a password.

    Public: this is how a first token becomes obtainable at all. The password is hashed
    server-side and no response from any endpoint ever contains it.
    """
    return await service.register(body)


# `/me` is declared **before** `/{user_id}`: Starlette matches in declaration order, and
# with the dynamic route first every request for `/v1/users/me` would be a failed attempt
# to parse "me" as a UUID.
@router.get(
    "/me",
    response_model=UserOut,
    summary="The signed-in account",
    responses=UNAUTHORIZED_RESPONSE,
)
async def read_current_user(user: CurrentUser, service: UserServiceDep) -> UserOut:
    """Who the bearer token belongs to, read fresh from the database on every call."""
    return await service.current_user(user=user)


@router.get(
    "/{user_id}",
    response_model=UserOut,
    summary="An account by id",
    responses={
        **UNAUTHORIZED_RESPONSE,
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": (
                "`not_found` — no such account, **or** it is not yours. The two are "
                "deliberately indistinguishable."
            ),
        },
    },
)
async def read_user(
    user_id: Annotated[uuid.UUID, Path(description="The account to read. Only your own resolves.")],
    user: CurrentUser,
    service: UserServiceDep,
) -> UserOut:
    """Resolve an account id — your own. Anybody else's is a 404, exactly like a missing one."""
    return await service.get_user(user_id=user_id, requester=user)


__all__ = ["router"]
