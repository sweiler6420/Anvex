"""S3 object storage, reached through ``aioboto3``. MinIO locally, AWS in production.

This is the first client in ``app/clients/`` that is **not** a :class:`~app.clients.base.
BaseHTTPClient`, and the split is deliberate rather than an omission. ``BaseHTTPClient`` is
*HTTP transport*: it builds an :class:`httpx.Request`, retries it, redacts its URL and decodes
its body. None of that is reachable through an SDK — ``aioboto3`` builds the request, signs
it, retries it and parses the XML, and there is no seam to hand it a ``httpx.AsyncClient``.
Subclassing to inherit a lifecycle and then overriding every method that uses it would be a
base class in name only.

What this module *does* share is the half that matters to everything above it, and
``CLAUDE.md`` §3 states it as the layer's contract rather than as the base class's behaviour:

* **one vendor, no Anvex knowledge.** Primitives in, vendor data out. There is no
  ``stock_id`` here and no idea what an "export" is — that is ``app/services/storage.py``,
  and where the key comes from is ``app/domain/storage.py``.
* **typed data out, never a raw response.** :class:`S3Object` and :class:`S3ObjectInfo`, not
  a ``botocore`` response dict full of ``ResponseMetadata``.
* **``ExternalServiceError`` is the one exit** (→ 502). No ``ClientError`` and no
  ``BotoCoreError`` escapes this module; a service that had to ``import botocore`` to handle
  a missing object would have the vendor's SDK in its own signature.
* **structured logging, no bare print, nothing secret written down.**

The ``Failure`` enum is *not* reused, and that is the considered answer
---------------------------------------------------------------------

:class:`~app.clients.base.Failure` is HTTP-shaped — ``server_error``, ``client_error``,
``unexpected_redirect``, ``malformed_response``. Forced onto S3 it collapses exactly the
distinctions a caller acts on: a missing key, a missing bucket and a rejected signature all
arrive as 404/404/403 and would all become ``client_error``, leaving a caller to parse a
message string to tell "this export has expired" from "the deployment has the wrong bucket
name" from "the credentials are wrong". The ticket's own test is the right one — those are
four different things and must be distinguishable in ``details``.

So :class:`S3Failure` is its own enum, and it is a :class:`~enum.StrEnum` for the same reason
``Failure`` is: the member's *value* is what lands in ``details["reason"]``. Where a member
means the same thing as an HTTP one it **reuses the same string** — ``transport_error``,
``server_error``, ``rate_limited``, ``client_error`` are spelled identically to
:class:`~app.clients.base.Failure`'s — so a consumer branching on ``details["reason"]`` sees
one vocabulary across the whole layer and only has to learn the S3-specific members
(``object_not_found``, ``bucket_not_found``, ``invalid_credentials``, ``access_denied``,
``sdk_error``). Sharing the vocabulary is the part that was worth sharing; sharing the enum
would have meant sharing ``unexpected_redirect``, which S3 cannot produce, and losing the
five members it can.

``details`` carries ``reason`` and, where the SDK reported one, ``status_code``. It carries
**no key and no bucket**, which is a narrower choice than it may look: ``CLAUDE.md`` §4 makes
the error body a public contract, and an export key contains the owner's UUID. Both are
logged, where they are the whole diagnosis and the audience is an operator.

Retries belong to ``botocore``, so there is no ``attempts`` key
--------------------------------------------------------------

``BaseHTTPClient`` implements its own retry loop because ``httpx`` has none. ``botocore``
does — its ``standard`` retry mode already knows which S3 error codes are transient
(``SlowDown``, ``RequestTimeout``, 5xx) and applies AWS's own backoff. Re-implementing that
on top would retry twice at every layer, so :data:`RETRY_ATTEMPTS` configures the SDK's loop
instead. The consequence is honest rather than convenient: ``botocore`` does not report how
many attempts it made, so ``details`` has **no** ``attempts`` key — the same rule ANV-18 set
when a body-detected failure had no attempt count of its own. Inventing a ``1`` would be
worse than an absent key.

Credentials, and why a blank one is refused before the call
----------------------------------------------------------

The credentials are passed to ``botocore`` **explicitly, on every client construction**, and
:meth:`S3Client._require_configuration` refuses before that when any of them is blank. That
pre-flight is not ceremony here the way it might be for a vendor with a local default: an
``aioboto3`` client built with no explicit credentials falls back to **botocore's default
credential chain** — environment variables, ``~/.aws/credentials``, an instance profile — so
a deployment (or a developer's laptop) with a blank ``S3_SECRET_ACCESS_KEY`` would not fail,
it would quietly authenticate as whatever real AWS identity happened to be lying around and
write to a real bucket. Refusing up front is what makes that impossible. The shape is
ANV-19's: ``details = {"reason": "not_configured", "setting": "<ENV_VAR>"}``, raised
directly rather than through :meth:`S3Client._error`, because every :class:`S3Failure`
describes how a *call* went wrong and no call was made.

The secret stays a :class:`~pydantic.SecretStr` on the instance for the client's whole life
and is unwrapped only where ``botocore`` has to be handed it — the SDK equivalent of
``CLAUDE.md`` §3's "unwrapped in the request builder and nowhere else".

Presigned URLs are produced and never logged
--------------------------------------------

:meth:`S3Client.presigned_get_url` returns a URL whose query string *is* a valid credential:
anyone holding it can fetch the object until it expires, with no further authentication. So
it is returned to the caller and written nowhere — the log line for a presign records the
key and the TTL and stops. ``tests/unit/test_clients_s3.py`` asserts the returned URL appears
in no captured log entry, because "we remembered not to" is not a mechanism. Whether such a
URL should ever leave the API is a separate question and **not** in this ticket's scope;
nothing here mounts a route.

Addressing style is pinned when a custom endpoint is configured
---------------------------------------------------------------

``botocore``'s default ``auto`` addressing turns a DNS-compatible bucket into a virtual-host
prefix — ``http://anvex-local.localhost:9000/…`` — which resolves nowhere. MinIO speaks
path-style, so :func:`_client_config` pins ``path`` whenever :attr:`Settings.s3_endpoint_url`
is set and leaves ``auto`` (AWS's preferred virtual-host style) when it is ``None``. That one
setting being ``None`` in production is the entire deploy-time difference between the two
environments, exactly as ``CLAUDE.md`` §4 intends.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from enum import StrEnum
from functools import lru_cache
from typing import Any, ClassVar, Final, Self, TypeVar

import aioboto3
import botocore.exceptions as boto_errors
import structlog
from botocore.config import Config
from pydantic import BaseModel, ConfigDict, SecretStr

from app.clients.base import scrub
from app.domain.errors import ExternalServiceError
from app.settings import Settings

logger = structlog.get_logger("anvex.clients")

T = TypeVar("T")

#: The AWS service this module speaks. One vendor per module, and this is the vendor.
SERVICE_NAME: Final[str] = "s3"

#: ``details["reason"]`` for the pre-flight refusal. Spelled the same as ANV-19's, because
#: an operator reading a 502 should not have to learn a second word for the same mistake.
NOT_CONFIGURED: Final[str] = "not_configured"

#: Seconds to wait for the endpoint's TCP/TLS handshake. Short: a handshake is fast or the
#: host is gone. Named separately from the read timeout for ``CLAUDE.md`` §3's reason — a
#: single number hides which one was meant. ``botocore`` exposes exactly these two.
CONNECT_TIMEOUT_SECONDS: Final[float] = 5.0

#: Seconds to wait for bytes once connected. Generous: an object may be large and S3 is
#: allowed to take a moment, and this bounds a *read*, not a whole transfer.
READ_TIMEOUT_SECONDS: Final[float] = 30.0

#: Total tries ``botocore`` makes for a retryable error, including the first. See the module
#: docstring: the retry loop is the SDK's, not ours.
RETRY_ATTEMPTS: Final[int] = 3

#: Connection pool size for one client instance. The pool is why a client is worth holding
#: for more than one call.
MAX_POOL_CONNECTIONS: Final[int] = 10


# ---------------------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------------------


class S3Failure(StrEnum):
    """Why an S3 call did not do what was asked. The value lands in ``details.reason``.

    See the module docstring for why this is not :class:`~app.clients.base.Failure`. The
    four members with an HTTP twin deliberately share its spelling.
    """

    #: The bucket exists and the object does not. The ordinary "this export is gone".
    NOT_FOUND = "object_not_found"
    #: The *bucket* does not exist. Almost always a misconfigured ``S3_BUCKET`` rather than
    #: anything about this call, which is why it must not read as a missing object.
    BUCKET_NOT_FOUND = "bucket_not_found"
    #: The credentials are absent, malformed, expired, or the signature did not verify.
    #: Distinct from :attr:`ACCESS_DENIED`: nobody is authenticated at all.
    INVALID_CREDENTIALS = "invalid_credentials"
    #: Authenticated, and not allowed to do this. A policy problem, not a credential one.
    ACCESS_DENIED = "access_denied"
    #: ``SlowDown`` / 503. A "not now", and the caller's cue to reschedule.
    RATE_LIMITED = "rate_limited"
    #: 5xx from the service itself.
    SERVER_ERROR = "server_error"
    #: Any other 4xx — a malformed request, a key S3 will not accept.
    CLIENT_ERROR = "client_error"
    #: The endpoint could not be reached, or the connection died mid-call.
    TRANSPORT = "transport_error"
    #: ``botocore`` refused before or after the wire: parameter validation, a broken
    #: response parse. Real, ours to fix, and never transient.
    SDK_ERROR = "sdk_error"


#: One sentence per failure. These are the strings an API consumer sees in a 502 body, so
#: they say what happened and nothing about how the request was built — no bucket, no key,
#: no endpoint.
_MESSAGES: Final[Mapping[S3Failure, str]] = {
    S3Failure.NOT_FOUND: "The upstream service '{vendor}' has no such object.",
    S3Failure.BUCKET_NOT_FOUND: "The upstream service '{vendor}' has no such bucket.",
    S3Failure.INVALID_CREDENTIALS: "The upstream service '{vendor}' rejected our credentials.",
    S3Failure.ACCESS_DENIED: "The upstream service '{vendor}' denied access.",
    S3Failure.RATE_LIMITED: "The upstream service '{vendor}' is rate limiting Anvex.",
    S3Failure.SERVER_ERROR: "The upstream service '{vendor}' failed.",
    S3Failure.CLIENT_ERROR: "The upstream service '{vendor}' rejected the request.",
    S3Failure.TRANSPORT: "The upstream service '{vendor}' could not be reached.",
    S3Failure.SDK_ERROR: "The upstream service '{vendor}' could not be spoken to.",
}

#: S3 error codes that name themselves. Taken from AWS's documented list plus the bare
#: numeric codes ``botocore`` synthesises for a ``HEAD``, which has no body to parse a real
#: code out of. A code not listed here falls back to :func:`_failure_for_status`.
_CODE_FAILURES: Final[Mapping[str, S3Failure]] = {
    "NoSuchKey": S3Failure.NOT_FOUND,
    "NotFound": S3Failure.NOT_FOUND,
    "404": S3Failure.NOT_FOUND,
    "NoSuchBucket": S3Failure.BUCKET_NOT_FOUND,
    "AccessDenied": S3Failure.ACCESS_DENIED,
    "AllAccessDisabled": S3Failure.ACCESS_DENIED,
    "403": S3Failure.ACCESS_DENIED,
    "InvalidAccessKeyId": S3Failure.INVALID_CREDENTIALS,
    "SignatureDoesNotMatch": S3Failure.INVALID_CREDENTIALS,
    "InvalidSecurity": S3Failure.INVALID_CREDENTIALS,
    "ExpiredToken": S3Failure.INVALID_CREDENTIALS,
    "InvalidToken": S3Failure.INVALID_CREDENTIALS,
    "AuthorizationHeaderMalformed": S3Failure.INVALID_CREDENTIALS,
    "SlowDown": S3Failure.RATE_LIMITED,
    "RequestThrottled": S3Failure.RATE_LIMITED,
    "RequestThrottledException": S3Failure.RATE_LIMITED,
    "TooManyRequests": S3Failure.RATE_LIMITED,
    "503": S3Failure.RATE_LIMITED,
    "InternalError": S3Failure.SERVER_ERROR,
    "ServiceUnavailable": S3Failure.SERVER_ERROR,
    "500": S3Failure.SERVER_ERROR,
}

#: ``botocore`` exception classes that mean "the bytes never made it". ``ConnectionError``
#: here is *botocore's*, not the builtin — it is the parent of ``EndpointConnectionError``
#: and ``ConnectTimeoutError``; ``HTTPClientError`` covers ``ReadTimeoutError`` and
#: ``ConnectionClosedError``. Between them that is the whole network family, which is the
#: same simplification ``BaseHTTPClient`` gets from ``httpx.TransportError``.
_TRANSPORT_ERRORS: Final[tuple[type[BaseException], ...]] = (
    boto_errors.ConnectionError,
    boto_errors.HTTPClientError,
)

#: ``botocore`` exception classes that mean "we never had a usable identity". Caught before
#: :data:`_TRANSPORT_ERRORS` would not matter — they share no ancestor below ``BotoCoreError``
#: — but they are listed separately because the answer differs.
_CREDENTIAL_ERRORS: Final[tuple[type[BaseException], ...]] = (
    boto_errors.NoCredentialsError,
    boto_errors.PartialCredentialsError,
)


def _failure_for_status(status: int | None) -> S3Failure:
    """Map an HTTP status onto the taxonomy, for a code :data:`_CODE_FAILURES` has no entry for."""
    if status is None:
        return S3Failure.SDK_ERROR
    if status == 429:
        return S3Failure.RATE_LIMITED
    if status >= 500:
        return S3Failure.SERVER_ERROR
    if status == 404:
        return S3Failure.NOT_FOUND
    if status == 403:
        return S3Failure.ACCESS_DENIED
    if status >= 400:
        return S3Failure.CLIENT_ERROR
    return S3Failure.SDK_ERROR


def classify(error: BaseException) -> tuple[S3Failure, str | None, int | None]:
    """``(failure, aws_error_code, http_status)`` for anything ``aioboto3`` can raise.

    A free function rather than a method, so the mapping — the part of this module most
    likely to be wrong — is testable by handing it a hand-built exception, with no client,
    no event loop and nothing patched.

    The order is the meaning: a :class:`~botocore.exceptions.ClientError` carries the
    service's own answer and is always the most specific thing available, so it is read
    first; credential and transport failures never reached the service and are recognised by
    type; anything else that is still a ``BotoCoreError`` is the SDK itself objecting.
    """
    if isinstance(error, boto_errors.ClientError):
        response = error.response if isinstance(error.response, Mapping) else {}
        error_body = response.get("Error") or {}
        metadata = response.get("ResponseMetadata") or {}
        code = error_body.get("Code")
        status = metadata.get("HTTPStatusCode")
        status = status if isinstance(status, int) else None
        code = str(code) if code is not None else None
        failure = _CODE_FAILURES.get(code or "") or _failure_for_status(status)
        return failure, code, status
    if isinstance(error, _CREDENTIAL_ERRORS):
        return S3Failure.INVALID_CREDENTIALS, None, None
    if isinstance(error, _TRANSPORT_ERRORS):
        return S3Failure.TRANSPORT, None, None
    return S3Failure.SDK_ERROR, None, None


# ---------------------------------------------------------------------------------------
# The typed results
# ---------------------------------------------------------------------------------------


class S3ObjectInfo(BaseModel):
    """What S3 knows about an object without sending its bytes.

    Returned by :meth:`S3Client.put_object` and :meth:`S3Client.head_object`. Everything but
    :attr:`key` is optional because it genuinely is: a ``PutObject`` response carries an
    ``ETag`` and no ``ContentType``, a ``HeadObject`` response carries both, and MinIO and
    AWS do not agree on every header.

    ``key`` is echoed back rather than left to the caller to remember, so a result is
    self-describing in a log or a test failure.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    #: Quoted by S3 itself (``"\"d41d8…\""``). Reported verbatim — unquoting it would be this
    #: layer reshaping a vendor value, and the quotes are part of the HTTP entity tag.
    etag: str | None = None
    size_bytes: int | None = None
    content_type: str | None = None
    last_modified: dt.datetime | None = None
    #: The user metadata S3 stored (``x-amz-meta-*``), lower-cased keys as S3 returns them.
    metadata: Mapping[str, str] = {}


class S3Object(BaseModel):
    """An object and its bytes, from :meth:`S3Client.get_object`.

    :attr:`body` is fully read before this model exists: the stream belongs to the
    connection, so handing it out would hand out a resource whose lifetime is the client's.
    A caller wanting a streaming download wants a presigned URL, which is
    :meth:`S3Client.presigned_get_url`.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    body: bytes
    etag: str | None = None
    content_type: str | None = None
    last_modified: dt.datetime | None = None
    metadata: Mapping[str, str] = {}

    @property
    def size_bytes(self) -> int:
        """The length actually received, not the length S3 claimed."""
        return len(self.body)


# ---------------------------------------------------------------------------------------
# Session and configuration
# ---------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def default_session() -> aioboto3.Session:
    """The process-wide ``aioboto3.Session``, built once.

    A ``Session`` is a factory and a cache, not a connection: it holds ``botocore``'s
    service-model loader, whose first ``s3`` lookup parses a multi-megabyte JSON model off
    disk. It owns no socket and is not bound to an event loop, so sharing one across
    requests — and across a ``fork``, which is what a Celery prefork worker does — is safe in
    a way sharing a *client* is not (see :mod:`app.deps.storage`).

    This is the cheap half of the client-lifetime question and the half that is unambiguously
    right: per-request construction of the expensive, loop-bound part stays, while the part
    that is expensive and *not* loop-bound stops being rebuilt. Injectable through
    :class:`S3Client`'s ``session`` argument so a test never touches this cache.
    """
    return aioboto3.Session()


def _client_config(settings: Settings) -> Config:
    """The ``botocore`` config every client is built with.

    ``addressing_style`` is the one line that differs between MinIO and AWS, and it differs
    because of the endpoint rather than because of a flag anyone sets — see the module
    docstring.
    """
    return Config(
        signature_version="s3v4",
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        read_timeout=READ_TIMEOUT_SECONDS,
        max_pool_connections=MAX_POOL_CONNECTIONS,
        retries={"max_attempts": RETRY_ATTEMPTS, "mode": "standard"},
        s3={"addressing_style": "path" if settings.s3_endpoint_url else "auto"},
    )


# ---------------------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------------------


class S3Client:
    """Anvex's S3 surface: put, get, head, delete, presign.

    Usage mirrors :class:`~app.clients.base.BaseHTTPClient` even though the inheritance does
    not::

        async with S3Client(settings) as storage:
            await storage.put_object("exports/…/a.csv", b"id,name\\n", content_type="text/csv")

    The underlying ``aioboto3`` client is created **lazily** on the first call and closed by
    :meth:`aclose`, and a call after closing raises rather than silently reopening — the same
    lifecycle rule and the same reason as the HTTP base: silently reconnecting hides a
    lifecycle bug in something meant to be long-lived.
    """

    #: The name that lands in ``details["service"]`` and in every log line.
    vendor: ClassVar[str] = "s3"

    def __init__(
        self,
        settings: Settings,
        *,
        session: aioboto3.Session | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._endpoint_url = settings.s3_endpoint_url
        self._region = settings.s3_region
        self._access_key_id = settings.s3_access_key_id
        # Stays a `SecretStr` for the client's whole life. Unwrapped only where botocore has
        # to be handed it, and never assigned to anything that outlives that expression.
        self._secret_access_key: SecretStr = settings.s3_secret_access_key
        self._bucket = settings.s3_bucket
        self._config = _client_config(settings)
        self._session = session if session is not None else default_session()
        self._clock = clock
        self._stack: AsyncExitStack | None = None
        self._client: Any | None = None
        self._closed = False

    # ----- lifecycle ---------------------------------------------------------------

    @property
    def bucket(self) -> str:
        """The bucket every operation on this client addresses.

        A client is bound to one bucket on purpose: the bucket is configuration, not an
        argument, so a caller cannot reach a different one by passing a different string.
        """
        return self._bucket

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def is_configured(self) -> bool:
        """Whether every credential and the bucket name are non-blank.

        Public for the same reason ``NewsApiClient.is_configured`` is — a caller may want to
        know whether the feature exists here without provoking a failure — and, as there,
        reading it is not a substitute for handling the error: a key can be present and still
        be rejected.
        """
        return not self._missing_setting()

    async def aclose(self) -> None:
        """Release the connection pool. Idempotent; a call afterwards raises."""
        self._closed = True
        stack, self._stack, self._client = self._stack, None, None
        if stack is not None:
            await stack.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ----- operations --------------------------------------------------------------

    async def put_object(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> S3ObjectInfo:
        """Write ``body`` at ``key``, replacing whatever was there.

        ``PutObject`` has no "fail if it exists" mode, so avoiding a silent overwrite is the
        caller's problem — ``app/domain/storage.export_key`` solves it with a unique suffix.

        :param content_type: sent as ``Content-Type``. Not defaulted from the key here:
            deciding that a ``.csv`` is ``text/csv; charset=utf-8`` is an Anvex rule and
            lives in ``app/domain/storage.py``.
        :param metadata: stored as ``x-amz-meta-*``. Values must be ASCII-safe header text;
            S3, not this module, is the authority on that.
        :raises ExternalServiceError: for every failure, including "not configured".
        """
        params: dict[str, Any] = {"Bucket": self._bucket, "Key": key, "Body": body}
        if content_type is not None:
            params["ContentType"] = content_type
        if metadata:
            params["Metadata"] = dict(metadata)

        response = await self._run("put_object", key, lambda client: client.put_object(**params))
        return S3ObjectInfo(
            key=key,
            etag=response.get("ETag"),
            size_bytes=len(body),
            content_type=content_type,
            metadata=dict(metadata or {}),
        )

    async def get_object(self, key: str) -> S3Object:
        """Read the whole object at ``key`` into memory.

        The body is read inside the same guarded call as the request, because a stream that
        dies halfway is as much a transport failure as a connection that never opened — and
        reading it afterwards would put an unguarded ``await`` outside the one place that
        turns a ``botocore`` exception into an :class:`~app.domain.errors.ExternalServiceError`.

        :raises ExternalServiceError: ``details["reason"] == "object_not_found"`` when there
            is no such key. That is the one a caller usually branches on.
        """

        async def fetch(client: Any) -> tuple[Mapping[str, Any], bytes]:
            response = await client.get_object(Bucket=self._bucket, Key=key)
            async with response["Body"] as stream:
                return response, await stream.read()

        response, body = await self._run("get_object", key, fetch)
        return S3Object(
            key=key,
            body=body,
            etag=response.get("ETag"),
            content_type=response.get("ContentType"),
            last_modified=response.get("LastModified"),
            metadata=dict(response.get("Metadata") or {}),
        )

    async def head_object(self, key: str) -> S3ObjectInfo:
        """Metadata for ``key`` without transferring it.

        :raises ExternalServiceError: as :meth:`get_object`, but see :meth:`object_exists`
            for the one wrinkle a ``HEAD`` has.
        """
        response = await self._run(
            "head_object", key, lambda client: client.head_object(Bucket=self._bucket, Key=key)
        )
        return S3ObjectInfo(
            key=key,
            etag=response.get("ETag"),
            size_bytes=response.get("ContentLength"),
            content_type=response.get("ContentType"),
            last_modified=response.get("LastModified"),
            metadata=dict(response.get("Metadata") or {}),
        )

    async def object_exists(self, key: str) -> bool:
        """Whether ``key`` is there. ``False`` is an answer; anything else still raises.

        **The honest caveat:** a ``HEAD`` response has no body, so ``botocore`` has no error
        code to parse and synthesises one from the status line — which means a missing
        *bucket* answers ``404`` exactly as a missing *key* does, and this returns ``False``
        for both. That is a property of the protocol, not a shortcut taken here: AWS
        documents the same 404 for both cases and real S3 behaves identically to MinIO. It is
        tolerable because the bucket is configuration rather than input — it is the same for
        every call — and because :meth:`get_object` and :meth:`put_object` *do* carry an
        error body and report ``bucket_not_found`` truthfully. A deployment pointed at a
        bucket that does not exist therefore fails loudly on its first real operation, not
        silently forever.

        Every other failure — denied, throttled, unreachable — propagates. Swallowing those
        into ``False`` would turn an outage into "the file is not there", which is the answer
        most likely to make a caller delete something.
        """
        try:
            await self.head_object(key)
        except ExternalServiceError as error:
            if error.details.get("reason") == S3Failure.NOT_FOUND.value:
                return False
            raise
        return True

    async def delete_object(self, key: str) -> None:
        """Remove ``key``.

        Returns ``None`` and does **not** report whether anything was there: S3's
        ``DeleteObject`` answers ``204`` for a key that never existed, so a truthful boolean
        would need a ``HEAD`` first and would still be a race. Idempotent deletion is the
        semantics S3 offers, so it is the semantics this method offers.
        """
        await self._run(
            "delete_object", key, lambda client: client.delete_object(Bucket=self._bucket, Key=key)
        )

    async def presigned_get_url(
        self, key: str, *, expires_in: int, filename: str | None = None
    ) -> str:
        """A URL that fetches ``key`` for ``expires_in`` seconds, with no further auth.

        The returned string contains a valid signature. **It is never logged** — see the
        module docstring — and the caller is responsible for treating it as a credential.

        :param expires_in: seconds. Bounded by ``app/domain/storage.resolve_download_ttl``,
            which is where the ceiling is a rule; this layer takes the number it is given,
            because "how long is too long" is an Anvex decision and not a fact about S3.
        :param filename: sets ``ResponseContentDisposition`` so a browser saves the download
            under a readable name instead of the key's last segment. Quoted here because a
            filename containing a space or a comma otherwise truncates the header value.
        :raises ExternalServiceError: for a bad parameter or an unconfigured client. Note it
            does **not** raise for a missing object: presigning is local arithmetic over the
            credentials and never contacts S3, so a URL for a key that does not exist is
            produced happily and 404s when it is used.
        """
        params: dict[str, Any] = {"Bucket": self._bucket, "Key": key}
        if filename is not None:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'

        return await self._run(
            "generate_presigned_url",
            key,
            lambda client: client.generate_presigned_url(
                "get_object", Params=params, ExpiresIn=expires_in
            ),
            log_result=False,
        )

    # ----- plumbing ----------------------------------------------------------------

    async def _run(
        self,
        operation: str,
        key: str | None,
        call: Callable[[Any], Awaitable[T]],
        *,
        log_result: bool = True,
    ) -> T:
        """Perform one SDK call, log it, and translate anything it raises.

        Every operation above goes through here, which is what makes "no ``botocore``
        exception escapes this module" a single fact rather than five identical ``try``
        blocks that must each be got right. ``log_result`` exists for exactly one caller:
        a presigned URL must not reach a log line even at ``debug``.
        """
        self._require_configuration()
        client = await self._connect()
        log = logger.bind(vendor=self.vendor, bucket=self._bucket, operation=operation, key=key)
        started = self._clock()
        log.debug("s3.request.started")
        try:
            result = await call(client)
        except Exception as error:
            # Deliberately broad. This is the single place a vendor exception becomes the
            # layer's one exit, so a class nobody anticipated must not escape as a 500.
            failure, code, status = classify(error)
            log.warning(
                "s3.request.failed",
                reason=failure.value,
                aws_error_code=code,
                status_code=status,
                error_type=type(error).__name__,
                error=self._scrub(str(error)),
                duration_ms=self._elapsed_ms(started),
            )
            raise self._error(failure, status_code=status) from error
        log.info(
            "s3.request.completed",
            duration_ms=self._elapsed_ms(started),
            **({} if log_result else {"result": "withheld"}),
        )
        return result

    async def _connect(self) -> Any:
        """The live ``aioboto3`` client, created on first use.

        ``session.client()`` is an async context manager, so an :class:`AsyncExitStack` holds
        it open for the client's life and :meth:`aclose` unwinds it. Credentials are passed
        explicitly on every construction — never left to ``botocore``'s default chain, which
        is the difference between "this deployment is misconfigured" and "this deployment
        quietly wrote to a real AWS account".
        """
        if self._closed:
            raise RuntimeError("S3Client is closed; construct a new one rather than reusing it.")
        if self._client is None:
            stack = AsyncExitStack()
            self._client = await stack.enter_async_context(
                self._session.client(
                    SERVICE_NAME,
                    endpoint_url=self._endpoint_url,
                    region_name=self._region,
                    aws_access_key_id=self._access_key_id,
                    aws_secret_access_key=self._secret_access_key.get_secret_value(),
                    config=self._config,
                )
            )
            self._stack = stack
        return self._client

    def _missing_setting(self) -> str | None:
        """The ``.env`` key an operator has to fill in, or ``None`` when all of them are set."""
        for value, setting in (
            (self._bucket, "S3_BUCKET"),
            (self._access_key_id, "S3_ACCESS_KEY_ID"),
            (self._secret_access_key.get_secret_value(), "S3_SECRET_ACCESS_KEY"),
        ):
            if not value.strip():
                return setting
        return None

    def _require_configuration(self) -> None:
        """Refuse before constructing a client, when a credential is blank.

        See the module docstring: the alternative is not a failed call, it is ``botocore``
        falling back to the ambient AWS identity.
        """
        setting = self._missing_setting()
        if setting is not None:
            raise ExternalServiceError(
                self.vendor,
                f"The upstream service '{self.vendor}' is not configured.",
                details={"reason": NOT_CONFIGURED, "setting": setting},
            )

    def _error(self, failure: S3Failure, *, status_code: int | None = None) -> ExternalServiceError:
        """Build the one exception this module raises.

        ``details`` gets ``service``, ``reason`` and — when the SDK reported one — the
        upstream ``status_code``. It gets no ``attempts`` (``botocore`` owns the retry loop
        and does not say), no bucket and no key: the error body is a public contract and an
        export key carries its owner's UUID.
        """
        details: dict[str, Any] = {"reason": failure.value}
        if status_code is not None:
            details["status_code"] = status_code
        return ExternalServiceError(
            self.vendor, _MESSAGES[failure].format(vendor=self.vendor), details=details
        )

    def _scrub(self, text: str) -> str:
        """A library's message, with our secret removed.

        The last line of defence, and the same helper ``BaseHTTPClient`` uses — this is the
        part of the base worth importing. ``botocore`` does not normally quote a secret key
        back, but ``AuthorizationHeaderMalformed`` and friends quote the *request* and
        nothing in this module gets to assume what an SDK will say.
        """
        return scrub(text, (self._access_key_id, self._secret_access_key.get_secret_value()))

    def _elapsed_ms(self, started: float) -> float:
        return round((self._clock() - started) * 1000, 2)


__all__ = [
    "CONNECT_TIMEOUT_SECONDS",
    "MAX_POOL_CONNECTIONS",
    "NOT_CONFIGURED",
    "READ_TIMEOUT_SECONDS",
    "RETRY_ATTEMPTS",
    "SERVICE_NAME",
    "S3Client",
    "S3Failure",
    "S3Object",
    "S3ObjectInfo",
    "classify",
    "default_session",
]
