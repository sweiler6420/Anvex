"""Pure Anvex business logic — no I/O of any kind (``CLAUDE.md`` §3).

The error hierarchy is re-exported here for convenience. Rule modules — ``auth`` today,
watchlist and indicator rules later — are reached by their own path
(``from app.domain.auth import ...``) rather than re-exported, so importing an error does
not drag in every rule module's dependencies.
"""

from app.domain.errors import (
    AnvexError,
    ConflictError,
    ExternalServiceError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)

__all__ = [
    "AnvexError",
    "ConflictError",
    "ExternalServiceError",
    "ForbiddenError",
    "NotFoundError",
    "UnauthorizedError",
    "ValidationError",
]
