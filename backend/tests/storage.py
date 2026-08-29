"""How the test suite reaches — and prepares — the compose ``minio`` container.

The object-storage twin of :mod:`tests.database`, and written to the same rules for the same
reasons:

* **The test endpoint is built here, not in ``app/settings.py``.** ``S3_ENDPOINT_URL``
  defaults to the *in-network* ``http://minio:9000`` because the app always runs in a
  container. pytest does not: the normal workflow is ``uv run python -m pytest`` on the host,
  which has to dial the published ``localhost:9000``. So the harness owns a separate,
  test-only ``BaseSettings`` reading the same repo-root ``.env`` (``CLAUDE.md`` §2 — still
  one file) and borrowing the credentials from the real :class:`~app.settings.Settings`,
  because MinIO genuinely shares them with the app.
* **Any failure to reach MinIO is a skip, never an error** (``CLAUDE.md`` §6). The default
  suite is green with Docker stopped, and a test that fails because a developer has no
  container running teaches them to ignore failures.
* **Each test gets its own bucket, dropped afterwards** — the analogue of
  ``throwaway_database_url``. That is worth the two extra API calls: it means the S3 tier
  neither depends on ``minio-init`` having run nor leaves objects in the developer's ``dev``
  bucket, and it makes "the bucket does not exist" a state a test can actually arrange.

**Nothing here can reach AWS.** Every client is constructed with an explicit
``endpoint_url`` pointing at localhost and explicit credentials, so ``botocore``'s default
credential chain is never consulted — which is the same guarantee ``app/clients/s3.py``
makes, for the same reason.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from functools import cache
from typing import Any

import aioboto3
import httpx
from botocore.config import Config
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.settings import ENV_FILE, Settings, get_settings

#: Seconds to wait for MinIO to answer its health probe. Short on purpose: with the
#: container stopped this is dead time on every developer's fast run.
PROBE_TIMEOUT_SECONDS: float = 3.0

#: MinIO's unauthenticated liveness endpoint. It answers ``200`` as soon as the server is up,
#: needs no credentials and touches no bucket, so a probe cannot fail for the wrong reason.
HEALTH_PATH: str = "/minio/health/live"

#: Prefix every throwaway bucket gets, so a leftover from a killed run is recognisable.
BUCKET_PREFIX: str = "anvex-test-"


class HarnessStorageSettings(BaseSettings):
    """Test-only view of how to reach MinIO from the host.

    Not named ``Test…`` — pytest would try to collect it as a test class.
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    #: The published host port from ``.env``. One number to change moves both the compose
    #: mapping and the client, exactly as ``POSTGRES_TEST_HOST_PORT`` does.
    minio_host_port: int = 9000
    #: Overrides the whole URL, for the in-network case (``http://minio:9000``) or a MinIO
    #: that is not on localhost. Left unset it is derived from the port above.
    s3_test_endpoint_url: str | None = Field(default=None)


@cache
def harness_settings() -> HarnessStorageSettings:
    """The harness's storage settings, read once per process."""
    return HarnessStorageSettings()


def endpoint_url() -> str:
    """Where the test suite dials MinIO."""
    harness = harness_settings()
    return harness.s3_test_endpoint_url or f"http://localhost:{harness.minio_host_port}"


def describe_target() -> str:
    """Human-readable endpoint, for skip messages."""
    return endpoint_url()


@cache
def unavailable_reason() -> str | None:
    """``None`` when MinIO answers, otherwise the reason to skip.

    Cached: one probe per session keeps the "Docker is stopped" path cheap, and the answer
    cannot change usefully mid-run.
    """
    try:
        response = httpx.get(f"{endpoint_url()}{HEALTH_PATH}", timeout=PROBE_TIMEOUT_SECONDS)
        response.raise_for_status()
    except Exception as exc:  # every failure to reach it is equally a skip
        return (
            f"no MinIO at {describe_target()} ({type(exc).__name__}: {exc}). "
            "Start it with `docker compose up -d minio`."
        )
    return None


def storage_settings(bucket: str) -> Settings:
    """Application settings pointed at the host-side MinIO and ``bucket``.

    Built by copying the real :class:`~app.settings.Settings` rather than constructing a
    second one, so the credentials stay the ones ``.env`` gives both the app and the
    container, and only the two things that genuinely differ on the host — the endpoint and
    the bucket — are overridden.
    """
    return get_settings().model_copy(
        update={"s3_endpoint_url": endpoint_url(), "s3_bucket": bucket}
    )


def unique_bucket_name() -> str:
    """A bucket name no other test is using.

    Lower-case hex only: S3 bucket names must be DNS-compatible, and MinIO enforces it.
    """
    return f"{BUCKET_PREFIX}{uuid.uuid4().hex[:16]}"


async def _with_client(operation: Any) -> Any:
    """Run ``operation(client)`` against MinIO with an explicit, localhost-only client."""
    settings = get_settings()
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=endpoint_url(),
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    ) as client:
        return await operation(client)


def create_bucket(name: str) -> None:
    """Create a throwaway bucket on the test MinIO."""

    async def run(client: Any) -> None:
        await client.create_bucket(Bucket=name)

    asyncio.run(_with_client(run))


def drop_bucket(name: str) -> None:
    """Delete every object in ``name`` and then the bucket itself.

    S3 refuses to delete a non-empty bucket, and there is no recursive delete — so the sweep
    is the teardown.
    """

    async def run(client: Any) -> None:
        paginator = client.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=name):
            keys = [{"Key": row["Key"]} for row in page.get("Contents", [])]
            if keys:
                await client.delete_objects(Bucket=name, Delete={"Objects": keys})
        await client.delete_bucket(Bucket=name)

    # Teardown must never mask the test's own failure: a bucket that was never created
    # (because the test blew up early) would otherwise turn one red test into two.
    with suppress(Exception):
        asyncio.run(_with_client(run))


__all__ = [
    "BUCKET_PREFIX",
    "HEALTH_PATH",
    "PROBE_TIMEOUT_SECONDS",
    "HarnessStorageSettings",
    "create_bucket",
    "describe_target",
    "drop_bucket",
    "endpoint_url",
    "harness_settings",
    "storage_settings",
    "unavailable_reason",
    "unique_bucket_name",
]
