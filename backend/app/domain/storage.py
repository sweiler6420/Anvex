"""Where an object lives in the bucket, and what it is — Anvex's rules, not S3's.

``app/clients/s3.py`` knows how to put bytes at a key. It deliberately does not know what a
*good* key looks like, because that is not a fact about S3: every one of the decisions below
("exports live under ``exports/``", "an export is partitioned by day", "a download link is
good for fifteen minutes") is a decision Anvex made and could change without S3 noticing.
``CLAUDE.md`` §3's rule of thumb settles it — a key layout would still be true written on
paper, so it is a domain rule and this module is pure.

That purity is what makes the awkward parts testable at all. A key is a string that ends up
in a URL, in a log line and in a lifecycle policy's prefix filter, and the ways it can go
wrong (``..`` climbing out of a prefix, a leading ``/`` producing an unnameable object, a
1 500-character key S3 rejects, a Windows path separator arriving from a filename) are
exactly the ways that are tedious to provoke through a bucket and trivial to provoke here.

Three groups of rule live here:

* **Naming** — :func:`export_key` and the prefixes it is built from, so a key is composed in
  one place and every consumer that has to *filter* on a prefix
  (:func:`export_prefix_for_owner`, :func:`export_prefix_for_day`) derives the same string
  from the same function rather than re-spelling it.
* **Content type** — :func:`content_type_for`, because "a ``.csv`` export is served as
  ``text/csv; charset=utf-8``" is a product decision about how a browser should treat our
  file, not something ``botocore`` has an opinion on. Anything unrecognised is
  ``application/octet-stream``: a download the browser refuses to render is a nuisance, a
  download it renders as the wrong thing is a bug.
* **Link lifetime** — :func:`resolve_download_ttl`, the presigned-URL analogue of
  ``app/schemas/pagination.resolve_page_limit``. A signature that outlives the reason it was
  issued is the whole risk of presigning, so the ceiling is a rule and not a caller's choice.

**Object expiry is deliberately not here and not in the app at all.** Deleting last month's
exports is a bucket lifecycle policy — declarative, enforced by S3 whether or not Anvex is
running, and therefore ``backend/infra/``'s job. What this module owes that policy is a
prefix it can filter on, which is why the day partition exists at all; a Celery task sweeping
the bucket would be re-implementing a feature we already pay for. :data:`EXPORT_RETENTION`
records the intended window so the policy and the code cannot drift apart silently.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from typing import Final

# ---------------------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------------------

#: Top-level prefix for everything a user can be handed back. A bucket shared with other
#: kinds of object (raw vendor payloads, say) stays navigable, and a lifecycle policy can
#: name this one prefix without catching anything else.
EXPORTS_PREFIX: Final[str] = "exports"

#: S3's own hard limit on a key, in UTF-8 bytes. Enforced here rather than discovered as a
#: ``KeyTooLongError`` from the vendor, because the caller that built an over-long key is
#: the only thing that can fix it.
MAX_KEY_BYTES: Final[int] = 1024

#: How long an export is expected to survive. Not enforced by this module — see the module
#: docstring; it is here so the number the bucket policy uses has one home.
EXPORT_RETENTION: Final[timedelta] = timedelta(days=30)

#: Longest a slug taken from user-supplied text may be, so a 300-character report title
#: cannot push a key past :data:`MAX_KEY_BYTES` on its own.
MAX_SLUG_LENGTH: Final[int] = 60

_SLUG_ALLOWED = re.compile(r"[^a-z0-9]+")
_EXTENSION_ALLOWED = re.compile(r"\A[a-z0-9]{1,12}\Z")
_RESOURCE_ALLOWED = re.compile(r"\A[a-z0-9][a-z0-9-]{0,31}\Z")

# ---------------------------------------------------------------------------------------
# Content types
# ---------------------------------------------------------------------------------------

#: Extension → what a browser should be told the bytes are. Explicit rather than
#: :mod:`mimetypes`, which reads the *developer's machine* — on Windows it consults the
#: registry, so ``.csv`` resolves to ``application/vnd.ms-excel`` on some hosts and
#: ``text/csv`` on others. A content type that depends on who ran the process is not a
#: content type.
CONTENT_TYPES: Final[dict[str, str]] = {
    "csv": "text/csv; charset=utf-8",
    "json": "application/json",
    "ndjson": "application/x-ndjson",
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "pdf": "application/pdf",
    "zip": "application/zip",
    "parquet": "application/vnd.apache.parquet",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "svg": "image/svg+xml",
}

#: What an unrecognised extension gets. Chosen so a browser *downloads* rather than guesses:
#: sniffing is how a ``.csv`` full of ``=cmd|…`` becomes a rendered document.
DEFAULT_CONTENT_TYPE: Final[str] = "application/octet-stream"

# ---------------------------------------------------------------------------------------
# Presigned link lifetime
# ---------------------------------------------------------------------------------------

#: What a download link gets when nobody asks for anything in particular. Long enough for a
#: slow connection to finish, short enough that a link pasted into a chat is dead by the
#: time anyone reads it.
DEFAULT_DOWNLOAD_TTL: Final[timedelta] = timedelta(minutes=15)

#: The ceiling, and the reason this function exists. A presigned URL carries a valid
#: signature and needs no further authentication, so its lifetime *is* its blast radius —
#: which makes the maximum a rule Anvex owns rather than a parameter a caller picks.
MAX_DOWNLOAD_TTL: Final[timedelta] = timedelta(hours=1)

#: The floor. Zero or negative would produce an already-expired signature, which fails as a
#: 403 from S3 minutes after the code that caused it has gone.
MIN_DOWNLOAD_TTL: Final[timedelta] = timedelta(seconds=30)


# ---------------------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------------------


def normalise_extension(value: str) -> str:
    """``"  .CSV "`` → ``"csv"``.

    :raises ValueError: if what is left is not a short alphanumeric run. An extension ends
        up after the last ``.`` of a key and, on the way out, in a ``Content-Disposition``
        filename; ``tar.gz``, ``../x`` and an empty string are all refused here rather than
        being escaped somewhere downstream.
    """
    cleaned = value.strip().lstrip(".").lower()
    if not _EXTENSION_ALLOWED.match(cleaned):
        raise ValueError(
            f"{value!r} is not a usable file extension: expected 1-12 characters of a-z0-9."
        )
    return cleaned


def slugify(value: str) -> str:
    """Collapse arbitrary text into the ``a-z0-9-`` run that may appear in a key.

    Used for the human-readable tail of an export key, which is the only part of a key a
    person ever reads. Runs of anything else become a single ``-``; the result is truncated
    to :data:`MAX_SLUG_LENGTH` and trimmed of edge dashes, so truncation cannot leave a key
    ending in ``-``.

    :raises ValueError: if nothing survives. A name of ``"???"`` is a caller bug — silently
        substituting ``"untitled"`` would put an unfindable object in the bucket and report
        success.
    """
    slug = _SLUG_ALLOWED.sub("-", value.strip().lower()).strip("-")[:MAX_SLUG_LENGTH].strip("-")
    if not slug:
        raise ValueError(f"{value!r} contains no characters usable in an object key.")
    return slug


def _resource(value: str) -> str:
    """The middle prefix segment — ``"watchlist"``, ``"stock-data"``. Validated, not slugified.

    Deliberately stricter than :func:`slugify`: a resource name is written by us, in code,
    and appears in every lifecycle policy and dashboard filter that mentions this prefix.
    Quietly rewriting a typo into a *different valid* prefix is how a policy stops matching.
    """
    cleaned = value.strip().lower()
    if not _RESOURCE_ALLOWED.match(cleaned):
        raise ValueError(
            f"{value!r} is not a usable resource segment: expected 1-32 characters of "
            "a-z0-9 and '-', starting with a-z0-9."
        )
    return cleaned


def export_prefix_for_owner(*, resource: str, owner_id: uuid.UUID) -> str:
    """``exports/<resource>/<owner>/`` — everything one user has of one kind.

    The trailing slash is part of the value on purpose: an S3 prefix filter is a plain
    string comparison, so ``exports/report/<uuid>`` without it also matches
    ``exports/report/<uuid>0…``. A prefix that can match a sibling is a data leak in a
    listing endpoint.
    """
    return f"{EXPORTS_PREFIX}/{_resource(resource)}/{owner_id}/"


def export_prefix_for_day(*, resource: str, owner_id: uuid.UUID, day: datetime) -> str:
    """One owner's exports of one kind for one UTC day.

    ``day`` must be timezone-aware for the same reason every other datetime in
    ``app/domain/`` must be (``CLAUDE.md`` §4): a naive value would be partitioned in
    whatever zone the server happens to run in, so the same instant would land in two
    different prefixes depending on the host.
    """
    return f"{export_prefix_for_owner(resource=resource, owner_id=owner_id)}{_day_path(day)}/"


def export_key(
    *,
    resource: str,
    owner_id: uuid.UUID,
    name: str,
    extension: str,
    now: datetime,
    unique: str,
) -> str:
    """The full key an export is written to.

    ``exports/<resource>/<owner>/<YYYY>/<MM>/<DD>/<HHMMSS>-<slug>-<unique>.<ext>``

    Four things are doing work in that shape:

    * the **owner segment** means "everything belonging to this user" is a prefix, which is
      what a delete-my-account job and a per-user listing both need;
    * the **day partition** is what a bucket lifecycle policy filters on (see the module
      docstring) and what keeps a listing from paging through a year to find yesterday;
    * the **time and slug** make a key readable in a console, which is the only reason
      anyone will ever be able to answer "what is this object";
    * the **unique suffix** makes two exports requested in the same second by the same user
      two objects rather than one silently overwriting the other. S3 ``PutObject`` has no
      "fail if exists", so uniqueness is the caller's problem and this is where it is solved.

    ``unique`` is **required**, and injected for the same reason ``now`` is: a
    ``uuid4()`` inside this function would make the module's output depend on something it
    was not given, and ``CLAUDE.md`` §3's "no I/O of any kind" covers reading entropy as
    surely as it covers reading a clock. The service generates both, once, and passes them
    down — which is also what lets a test assert an entire key rather than a regex.

    :param now: timezone-aware. See :func:`export_prefix_for_day`.
    :raises ValueError: for a naive ``now``, an unusable segment, or a key over
        :data:`MAX_KEY_BYTES`.
    """
    stamp = _require_aware(now).strftime("%H%M%S")
    prefix = export_prefix_for_day(resource=resource, owner_id=owner_id, day=now)
    return validate_key(
        f"{prefix}{stamp}-{slugify(name)}-{slugify(unique)}.{normalise_extension(extension)}"
    )


def owner_of_export_key(key: str) -> uuid.UUID | None:
    """The owner :func:`export_key` encoded in ``key``, or ``None`` if it encoded none.

    The inverse of the third segment of :func:`export_key`, and the reason the owner is in
    the key at all: it lets ``app/services/storage.py`` answer "is this caller's" from the
    key alone, with no listing, no metadata fetch and no round trip — which is what
    ``CLAUDE.md`` §4 requires of an ownership refusal ("returns it *without querying*, so
    response time does not answer the question either").

    ``None`` for anything that is not an export key or does not carry a parseable UUID.
    Deliberately not an exception: the caller's next move is the same 404 either way, and a
    gate that raises for a malformed key and returns for a foreign one has two shapes of
    refusal where it should have one.
    """
    segments = key.split("/")
    if len(segments) < 4 or segments[0] != EXPORTS_PREFIX:
        return None
    try:
        return uuid.UUID(segments[2])
    except ValueError:
        return None


def validate_key(key: str) -> str:
    """Return ``key`` unchanged, or explain why it is not a key Anvex will write.

    The refusals are the point, and each is a real failure mode rather than a style rule:

    * **empty** — S3 accepts no such object;
    * **leading ``/``** — produces an object whose name starts with an empty segment, which
      most tooling then cannot address;
    * **``\\``** — a Windows path separator that arrived from a filename and is a literal
      character in a key, not a separator, so it silently makes a differently-named object;
    * **a ``.`` or ``..`` segment** — the traversal case. S3 keys are flat strings and do not
      normalise, so ``a/../b`` and ``b`` are two *different* objects; the danger is not that
      S3 climbs out of the prefix but that a consumer which *does* normalise (a browser, a
      local file write of the download) climbs out on S3's behalf;
    * **an empty segment or trailing ``/``** — a "directory marker", not a file;
    * **a control character** — survives into log lines and HTTP headers;
    * **over :data:`MAX_KEY_BYTES` UTF-8 bytes** — S3's own limit, counted in bytes because
      a key of 900 multibyte characters passes a character count and fails at the vendor.
    """
    if not key:
        raise ValueError("an object key may not be empty.")
    if key.startswith("/"):
        raise ValueError(f"{key!r} may not start with '/': the first segment would be empty.")
    if "\\" in key:
        raise ValueError(f"{key!r} contains a backslash, which is a literal character in a key.")
    if any(character < " " or character == "\x7f" for character in key):
        raise ValueError("an object key may not contain control characters.")
    segments = key.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise ValueError(f"{key!r} contains an empty or relative path segment.")
    if len(key.encode("utf-8")) > MAX_KEY_BYTES:
        raise ValueError(f"an object key may not exceed {MAX_KEY_BYTES} UTF-8 bytes.")
    return key


# ---------------------------------------------------------------------------------------
# Content types
# ---------------------------------------------------------------------------------------


def content_type_for(value: str) -> str:
    """The content type for an extension, a filename, or a key.

    Accepts all three because callers naturally hold different ones — a service knows the
    extension it asked for, a re-upload path knows a filename. Anything unrecognised is
    :data:`DEFAULT_CONTENT_TYPE`; an unrecognised *shape* (no extension at all) is too,
    rather than a ``ValueError``, because guessing wrong here is not worth failing a request
    that is otherwise fine.
    """
    candidate = value.rsplit("/", 1)[-1]
    candidate = candidate.rsplit(".", 1)[-1] if "." in candidate else candidate
    try:
        extension = normalise_extension(candidate)
    except ValueError:
        return DEFAULT_CONTENT_TYPE
    return CONTENT_TYPES.get(extension, DEFAULT_CONTENT_TYPE)


# ---------------------------------------------------------------------------------------
# Presigned link lifetime
# ---------------------------------------------------------------------------------------


def resolve_download_ttl(requested: timedelta | None = None) -> int:
    """Seconds a presigned download link should live for, clamped into the allowed band.

    Shaped exactly like ``resolve_page_limit``: ``None`` means the default, and an
    out-of-range request is **clamped rather than refused**, because the callers with no HTTP
    request to reject (a Celery task, a script) should still get a usable link. A route that
    ever exposes this to a user puts its own ``Query(le=…)`` in front, so an HTTP caller is
    told its number was too big instead of being quietly given a shorter one — the same
    two-layer argument ``CLAUDE.md`` §4 makes for the page limit, and it matters more here
    because the number is a security boundary.

    Returns whole seconds because that is what ``generate_presigned_url`` takes; a fractional
    TTL is not a thing S3 can express.
    """
    ttl = DEFAULT_DOWNLOAD_TTL if requested is None else requested
    ttl = min(max(ttl, MIN_DOWNLOAD_TTL), MAX_DOWNLOAD_TTL)
    return int(ttl.total_seconds())


# ---------------------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------------------


def _require_aware(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError("a timezone-aware datetime is required; a naive one has no UTC day.")
    return moment


def _day_path(day: datetime) -> str:
    """``2024/03/01`` — zero-padded so a lexical listing is a chronological listing."""
    return _require_aware(day).strftime("%Y/%m/%d")


__all__ = [
    "CONTENT_TYPES",
    "DEFAULT_CONTENT_TYPE",
    "DEFAULT_DOWNLOAD_TTL",
    "EXPORTS_PREFIX",
    "EXPORT_RETENTION",
    "MAX_DOWNLOAD_TTL",
    "MAX_KEY_BYTES",
    "MAX_SLUG_LENGTH",
    "MIN_DOWNLOAD_TTL",
    "content_type_for",
    "export_key",
    "export_prefix_for_day",
    "export_prefix_for_owner",
    "normalise_extension",
    "owner_of_export_key",
    "resolve_download_ttl",
    "slugify",
    "validate_key",
]
