"""The exception hierarchy the service layer raises.

``CLAUDE.md`` §3 forbids services from raising ``HTTPException``: a service is reused by
API handlers *and* Celery tasks, and an HTTP concept has no meaning in a task. Services
raise the errors below instead, and ``app/middleware/errors.py`` is the single place that
turns them into HTTP responses.

This module is ``domain/``, so it stays pure — standard library only. It imports no web
framework, no settings, no database, and carries no status codes; the status mapping
lives in the middleware where HTTP belongs.

Every error carries three things the error response is built from:

``code``
    A stable machine-readable slug (``"not_found"``). Clients branch on this, never on
    the human message.
``message``
    A human-readable sentence, safe to show to an API consumer.
``details``
    An optional flat ``dict`` of structured context (``{"resource": "stock",
    "identifier": "AAPL"}``). It is serialised to the client, so never put a secret,
    a stack trace, or raw upstream output in it.
"""

from __future__ import annotations

from typing import Any


class AnvexError(Exception):
    """Base class for every error Anvex raises on purpose.

    Catching :class:`AnvexError` catches all of them; anything else escaping a service is
    a bug and becomes an opaque 500.
    """

    #: Machine-readable slug emitted as ``error.code``. Overridden by every subclass.
    code: str = "internal_error"
    #: Used when the caller does not supply a message.
    default_message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details: dict[str, Any] = dict(details) if details else {}
        super().__init__(self.message)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.message!r}, details={self.details!r})"


class _ResourceError(AnvexError):
    """Shared constructor for errors that are *about* a specific resource.

    Subclasses only supply ``code`` and a sentence template, so ``NotFoundError("stock",
    "AAPL")`` and ``ConflictError("user", "a@b.com")`` read the same way and produce the
    same ``details`` keys.
    """

    #: ``"{resource} '{identifier}' was not found."`` — the tail after the subject.
    predicate: str = "is invalid"

    def __init__(
        self,
        resource: str | None = None,
        identifier: Any | None = None,
        *,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.resource = resource
        self.identifier = identifier
        merged: dict[str, Any] = {}
        if resource is not None:
            merged["resource"] = resource
        if identifier is not None:
            merged["identifier"] = str(identifier)
        if details:
            merged.update(details)
        super().__init__(message or self._describe(), details=merged)

    def _describe(self) -> str:
        if self.resource is None:
            return self.default_message
        subject = self.resource
        if self.identifier is not None:
            subject = f"{subject} '{self.identifier}'"
        return f"{subject} {self.predicate}."


class NotFoundError(_ResourceError):
    """A resource the caller asked for does not exist.

    ``NotFoundError("stock", "AAPL")`` → ``"stock 'AAPL' was not found."``
    """

    code = "not_found"
    default_message = "The requested resource was not found."
    predicate = "was not found"


class ConflictError(_ResourceError):
    """The request cannot be applied because it clashes with existing state.

    Duplicate email on signup, a watchlist name already taken, an optimistic-lock miss.
    ``ConflictError("user", "a@b.com")`` → ``"user 'a@b.com' already exists."``
    """

    code = "conflict"
    default_message = "The request conflicts with the current state of the resource."
    predicate = "already exists"


class ValidationError(AnvexError):
    """A *business* rule rejected the input.

    Distinct from pydantic's request-shape validation, which FastAPI handles before a
    service is ever called. This is "the reorder list is missing a stock id" or "the end
    date precedes the start date" — well-formed input that breaks a rule.
    """

    code = "validation_error"
    default_message = "The request is not valid."

    def __init__(
        self,
        message: str | None = None,
        *,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.field = field
        merged: dict[str, Any] = {"field": field} if field is not None else {}
        if details:
            merged.update(details)
        super().__init__(message, details=merged)


class UnauthorizedError(AnvexError):
    """The caller is not authenticated: no credentials, or bad/expired ones.

    Deliberately vague by default — telling an attacker *which* half of a credential pair
    was wrong is a gift.
    """

    code = "unauthorized"
    default_message = "Authentication is required or the provided credentials are invalid."


class ForbiddenError(AnvexError):
    """The caller is authenticated but not allowed to do this.

    Raised for another user's watchlist, an admin-only action, a disabled account.
    """

    code = "forbidden"
    default_message = "You do not have permission to perform this action."


class ExternalServiceError(AnvexError):
    """A third-party dependency failed: AlphaVantage, NewsAPI, S3.

    Raised by ``app/clients/`` (or a service wrapping one) for timeouts, 5xx responses and
    unparseable payloads. ``ExternalServiceError("alphavantage")`` →
    ``"The upstream service 'alphavantage' failed."`` The upstream's own body never
    belongs in ``details`` — log it, do not forward it.
    """

    code = "external_service_error"
    default_message = "An upstream service failed."

    def __init__(
        self,
        service: str | None = None,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.service = service
        merged: dict[str, Any] = {"service": service} if service is not None else {}
        if details:
            merged.update(details)
        if message is None and service is not None:
            message = f"The upstream service '{service}' failed."
        super().__init__(message, details=merged)


__all__ = [
    "AnvexError",
    "ConflictError",
    "ExternalServiceError",
    "ForbiddenError",
    "NotFoundError",
    "UnauthorizedError",
    "ValidationError",
]
