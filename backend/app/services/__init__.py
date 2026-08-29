"""Orchestration — the layer that gets things done (``CLAUDE.md`` §3).

A service composes repos, clients and domain functions into one use case, and owns two
things nothing below it may own: the transaction boundary, and the error semantics. It is
the only layer allowed to talk to more than one other layer, and the only layer allowed to
read a clock or unwrap a ``SecretStr``.

The shape established by :class:`~app.services.auth.AuthService` and copied by every
service after it:

* constructed with an ``AsyncSession``, a :class:`~app.settings.Settings`, and its repos as
  keyword arguments defaulting to the module-level singletons — so a unit test can pass
  fakes and never touch Postgres;
* one ``async`` method per use case, keyword-only arguments, returning a **schema** rather
  than an ORM row;
* raises ``app.domain.errors`` exceptions, **never** ``HTTPException`` — a service is
  reused by Celery, where HTTP has no meaning;
* reads ``datetime.now(UTC)`` **once** at the top of a method and passes that one value
  down into ``app/domain/``.
"""

from app.services.auth import AuthService
from app.services.user import UserService

__all__ = ["AuthService", "UserService"]
