"""Storing and retrieving a user's exports — the Anvex-meaningful half of object storage.

``app/clients/s3.py`` can put bytes at a key. That is not a feature. This module is what the
application actually *does* with a bucket: it names the object, decides what it is, decides
who may read it back, and decides which S3 failures are Anvex failures.

Four decisions live here and nowhere else, and each is in this layer for a reason
``CLAUDE.md`` §3 already gives:

1. **The key comes from ``app/domain/storage.py``.** A key layout is a pure rule, so the
   service composes it rather than inventing it — and it supplies both impure inputs the
   rule refuses to read for itself, the clock and the uniqueness token, once per call.
2. **The content type comes from the same place**, for the same reason.
3. **Ownership is a prefix comparison, and a foreign key is a 404.** ``CLAUDE.md`` §4: a
   refusal that would confirm the resource exists is a 404, not a 403, byte-identical to the
   answer for a key that never existed and reached *without querying*. Here that is free —
   the owner is in the key, so :func:`~app.domain.storage.owner_of_export_key` answers before
   a single byte crosses the network. Every use case goes through the one gate
   :meth:`StorageService._resolve_owned`, which is §4's "exactly one ownership gate" rule,
   and ``tests/unit/test_services_storage.py`` derives its sweep from ``vars(StorageService)``
   so a use case added without an isolation test fails the suite.
4. **``object_not_found`` becomes ``NotFoundError``; every other S3 failure stays a 502.**
   This is the one translation worth arguing for. ``app/clients/`` has exactly one exit and
   it is ``ExternalServiceError`` — correct, because a client cannot know whether a missing
   object is an outage or an ordinary absence. A *service* can: an export that is not there
   is a missing resource, and answering 502 for it would tell a user Anvex is broken when
   nothing is. Every other reason — denied, throttled, unreachable, misconfigured bucket —
   genuinely is "the upstream is unusable from here" and passes through untouched. The
   translation keys on ``details["reason"]``, never on the message, which is the whole reason
   ANV-17 put a machine-readable reason in ``details``.

**There is no session and no repo, and that is deliberate.** Every other service in the repo
takes an ``AsyncSession`` because every other service persists something; this one's entire
state lives in the bucket. Taking a session it never used would be a lie in the signature and
would make a Celery task open a database connection to write a file. If a later ticket adds
an ``exports`` table — a row per export so a user can list their downloads without a
``ListObjectsV2`` — the session arrives then, along with the repo that needs it.

**No route is mounted, on purpose.** :meth:`StorageService.download_url` produces a presigned
URL, and whether such a URL should ever leave the API is a question with real answers on both
sides (it bypasses Anvex's auth for its lifetime; it also keeps a large download off the API
process entirely). ANV-20's scope stops at being able to make one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict

from app.clients.s3 import S3Client, S3Failure
from app.domain import storage as keys
from app.domain.errors import ExternalServiceError, NotFoundError
from app.settings import Settings

#: The noun a 404 from this service names. One word, and the same one whether the export is
#: absent, foreign or never existed — which is what makes those three indistinguishable.
RESOURCE: str = "export"


class StoredExport(BaseModel):
    """What an upload produced: enough to fetch it again, and nothing else.

    Not an ``app/schemas/`` model, because nothing returns it over HTTP yet and
    ``app/schemas/`` is the API's *public* shape — adding one there would publish a contract
    for an endpoint that does not exist. ``CLAUDE.md`` §3 allows a service to return "a model
    another service consumes", which is exactly what a Celery job in ANV-22 will do with it.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    content_type: str
    size_bytes: int
    etag: str | None = None


class StorageService:
    """Exports: write one, read one back, check it, delete it, hand out a link to it."""

    def __init__(self, settings: Settings, *, client: S3Client) -> None:
        self.settings = settings
        #: Required rather than keyword-defaulted, unlike a repo: a client owns a connection
        #: pool and therefore a lifetime, so there is no module-level singleton to default
        #: to. ``app/deps/storage.py`` builds and closes one per request.
        self.client = client

    # ----- use cases ---------------------------------------------------------------

    async def store_export(
        self,
        *,
        owner_id: uuid.UUID,
        resource: str,
        name: str,
        extension: str,
        body: bytes,
    ) -> StoredExport:
        """Write ``body`` as a new export belonging to ``owner_id``.

        The clock is read **once**, here, and passed down — ``CLAUDE.md`` §4, and the reason
        the key's day partition and its timestamp cannot disagree. The uniqueness token is
        generated here for the same reason: ``app/domain/storage.py`` may not read entropy
        any more than it may read a clock, so both arrive as arguments.

        Two exports requested in the same second by the same user are two objects, never one
        overwriting the other — ``PutObject`` has no "fail if exists", so the uniqueness has
        to be in the name.

        :param resource: what kind of export this is (``"watchlist"``, ``"stock-data"``). It
            becomes a key segment and a lifecycle-policy prefix, so it is validated rather
            than slugified — see :func:`app.domain.storage.export_key`.
        :param name: free text from the caller, slugified into the readable tail.
        :raises ValueError: for an unusable segment or extension, before anything is sent.
        :raises ExternalServiceError: if S3 refuses the write.
        """
        now = datetime.now(UTC)
        key = keys.export_key(
            resource=resource,
            owner_id=owner_id,
            name=name,
            extension=extension,
            now=now,
            unique=uuid.uuid4().hex[:8],
        )
        content_type = keys.content_type_for(extension)
        info = await self.client.put_object(key, body, content_type=content_type)
        return StoredExport(
            key=key, content_type=content_type, size_bytes=len(body), etag=info.etag
        )

    async def read_export(self, *, owner_id: uuid.UUID, key: str) -> bytes:
        """The bytes of one of ``owner_id``'s exports.

        :raises NotFoundError: if the key is not this owner's, or the object is gone. The two
            are the same 404 with the same body — see the module docstring.
        """
        self._resolve_owned(key, owner_id)
        try:
            return (await self.client.get_object(key)).body
        except ExternalServiceError as error:
            # The one S3 failure that is not an outage. Keyed on the machine-readable
            # reason, never on the message — see the module docstring.
            if error.details.get("reason") == S3Failure.NOT_FOUND.value:
                raise NotFoundError(RESOURCE, key) from error
            raise

    async def export_exists(self, *, owner_id: uuid.UUID, key: str) -> bool:
        """Whether one of ``owner_id``'s exports is still there.

        A key belonging to somebody else answers ``False`` rather than raising, because this
        is the one use case whose *normal* answer is already a boolean: raising a 404 for a
        foreign key and returning ``False`` for an absent one would make the two
        distinguishable, which is exactly what the ownership rule forbids.
        """
        if keys.owner_of_export_key(key) != owner_id:
            return False
        return await self.client.object_exists(key)

    async def delete_export(self, *, owner_id: uuid.UUID, key: str) -> None:
        """Remove one of ``owner_id``'s exports.

        Idempotent, because S3's ``DeleteObject`` is: deleting a key that is already gone
        succeeds. A key belonging to somebody else is a 404 and deletes nothing — the gate
        runs before the call, so a foreign key never reaches the bucket.
        """
        self._resolve_owned(key, owner_id)
        await self.client.delete_object(key)

    async def download_url(
        self, *, owner_id: uuid.UUID, key: str, ttl: timedelta | None = None
    ) -> str:
        """A time-limited URL that fetches one of ``owner_id``'s exports directly from S3.

        **The returned string is a credential.** It carries a valid signature and needs no
        Anvex token, so it must not be logged, stored or put in an error body; the client
        keeps it out of its own logs and this layer adds nothing.

        The object's existence is checked first, which presigning itself does not do —
        ``generate_presigned_url`` is local arithmetic and will happily sign a key that was
        deleted last week, producing a link that 404s at the vendor with no explanation. One
        ``HEAD`` buys a truthful ``NotFoundError`` instead.

        :param ttl: clamped into ``app/domain/storage.py``'s allowed band; ``None`` takes the
            default. The ceiling is a rule rather than a caller's choice because the lifetime
            of a signature *is* its blast radius.
        """
        self._resolve_owned(key, owner_id)
        if not await self.client.object_exists(key):
            raise NotFoundError(RESOURCE, key)
        return await self.client.presigned_get_url(key, expires_in=keys.resolve_download_ttl(ttl))

    # ----- the gate ----------------------------------------------------------------

    def _resolve_owned(self, key: str, owner_id: uuid.UUID) -> str:
        """``key``, if it belongs to ``owner_id``. Otherwise the same 404 as a missing one.

        The single ownership gate every use case goes through (``CLAUDE.md`` §4). It costs no
        I/O — the owner is a segment of the key — so a refusal does no work proportional to
        anything, and a caller cannot learn from the response *or* from its timing whether
        the key exists.
        """
        if keys.owner_of_export_key(key) != owner_id:
            raise NotFoundError(RESOURCE, key)
        return key


__all__ = ["RESOURCE", "StorageService", "StoredExport"]
