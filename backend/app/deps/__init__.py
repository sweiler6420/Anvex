"""Reusable FastAPI dependencies (``CLAUDE.md`` §3).

Dependencies wire objects together; they never implement logic. Two families live here:

* **Resources** — ``get_session``, ``get_settings_dep``: one request-scoped thing each.
* **Service factories** — ``get_auth_service`` and its successors: resolve the resources a
  service needs and construct it. Every resource from ANV-12 onward adds one of these plus
  an ``XServiceDep`` alias, so a handler's signature stays a single annotated parameter and
  a route contract test can swap the whole service out with one override.

``CurrentUser`` is the annotation a protected route uses.
"""

from app.deps.auth import (
    AuthServiceDep,
    CurrentUser,
    get_auth_service,
    get_current_user,
    oauth2_scheme,
)
from app.deps.session import get_session
from app.deps.settings import get_settings_dep
from app.deps.user import UserServiceDep, get_user_service

__all__ = [
    "AuthServiceDep",
    "CurrentUser",
    "UserServiceDep",
    "get_auth_service",
    "get_current_user",
    "get_session",
    "get_settings_dep",
    "get_user_service",
    "oauth2_scheme",
]
