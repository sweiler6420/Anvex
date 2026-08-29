"""The storage service factory — wiring, and the client-lifetime decision.

:func:`get_storage_service` resolves a :class:`~app.settings.Settings` and an
:class:`~app.clients.s3.S3Client` and constructs the service. Nothing is decided here about
*behaviour*, which is what makes it the one seam a route contract test would override. It
takes no session: ``app/services/storage.py`` explains why there is nothing to persist yet.

The client-lifetime question, answered
--------------------------------------

``app/deps/news.py`` left one open: it builds and closes a ``NewsApiClient`` per request,
giving up the cross-request connection pooling ``CLAUDE.md`` §3 names as the reason a client
owns its transport at all, and it said the decision belonged to whichever ticket produced a
**second** caller — an application-scoped client owned by the lifespan and shared with the
Celery worker. This is that second caller, and after looking at both, the answer is **still
per-request — but the shared part has been split out, and the reason for not sharing the rest
is stronger for S3 than it was for NewsAPI.**

**Why not a lifespan-owned client.** An ``aiobotocore`` client is not merely stateful, it is
*bound to an event loop*: it owns an ``aiohttp`` connector whose sockets, timers and
transports belong to the loop that created them. That has two consequences the HTTP case did
not make visible.

* **The Celery half of the plan does not work.** The whole point of hoisting a client into
  the lifespan was to share one with the worker. A Celery prefork worker ``fork()``s after
  the parent has imported and (in any arrangement where the parent warms it) constructed
  things; a forked ``aiohttp`` connector inherits *file descriptors* for sockets that the
  parent also still holds, and two processes reading one TLS connection is not a race that
  fails loudly — it corrupts responses. And each Celery task runs its own ``asyncio.run``,
  i.e. its own loop, so a client created outside it is bound to a loop that is already
  closed. A lifespan-owned client is therefore unshareable with the worker by construction,
  which removes the benefit the generalisation existed to buy.
* **A shared client is a shared failure.** One client is one pool and one credential set. A
  connection reset that poisons it poisons every request until the process restarts, where a
  per-request client's blast radius is one response.

**What was actually shared, because it is genuinely shareable.** The expensive part of
building an S3 client is not the socket — no socket is opened until the first call — it is
``botocore`` parsing its ``s3`` service model, a multi-megabyte JSON document, and that
parsing is cached on the ``Session``. So :func:`~app.clients.s3.default_session` is a
process-wide ``lru_cache``d ``aioboto3.Session``, which holds **no** socket and is bound to
**no** loop, and is therefore safe across requests, across loops and across a ``fork``. That
is the half of "share the client" that was real; the loop-bound half was the half that could
not be shared anyway.

So the deferral is not the same deferral repeated. The measurable cost ANV-19 accepted has
been removed, and what remains — one TCP/TLS handshake per request against S3 — is bought
back at the point where it is actually paid, which is an ingest job that makes many calls
inside **one** ``async with S3Client(...)`` block rather than many requests making one each.
ANV-21 and ANV-22 should construct a client per task and keep it for the task's whole life;
they must not construct one at module import or in a worker-boot hook.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends

from app.clients.s3 import S3Client
from app.deps.settings import Settings, get_settings_dep
from app.services.storage import StorageService


async def get_s3_client(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> AsyncIterator[S3Client]:
    """An S3 client for this request, closed when the response is done.

    Constructing one is cheap and opens no socket: the ``aioboto3`` client is created lazily
    on the first call and the ``Session`` behind it is process-wide, so a request that never
    touches the bucket — a 404 from the ownership gate, say — costs nothing here.
    """
    client = S3Client(settings)
    try:
        yield client
    finally:
        await client.aclose()


def get_storage_service(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    client: Annotated[S3Client, Depends(get_s3_client)],
) -> StorageService:
    """Build a :class:`~app.services.storage.StorageService` for this request."""
    return StorageService(settings, client=client)


#: The annotation a handler would use, so a route signature stays one parameter. No route
#: mounts it yet — see ``app/services/storage.py`` on why presigned URLs are out of scope.
StorageServiceDep = Annotated[StorageService, Depends(get_storage_service)]

__all__ = ["StorageServiceDep", "get_s3_client", "get_storage_service"]
