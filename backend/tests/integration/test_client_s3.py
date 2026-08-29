"""``app/clients/s3.py`` against the real thing — the compose ``minio`` container.

The unit tier proves the *translation* (which ``botocore`` exception becomes which reason,
what a presign request is assembled out of) with the SDK patched out. This tier proves the
parts a fake cannot: that the request we assemble is one MinIO accepts, that path-style
addressing is actually required and actually works, that a presigned URL a real server signed
is a URL that really retrieves the object, and that a missing key really does come back as
``NoSuchKey`` rather than as whatever we guessed.

**Every test here skips when MinIO is not running.** Requesting ``s3_client`` (or any other
``s3_*`` fixture) auto-applies the ``s3`` marker and the skip, exactly as the ``db_*``
fixtures do for Postgres — so ``uv run python -m pytest`` stays green with Docker stopped, as
``CLAUDE.md`` §6 requires. Each test also gets its **own bucket**, created and dropped by the
fixture, so nothing here can see another test's objects or leave anything behind.

Nothing in this file can reach AWS: every client is built from
:func:`tests.storage.storage_settings`, whose ``s3_endpoint_url`` points at ``localhost``.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from pydantic import SecretStr
from structlog.testing import capture_logs

from app.clients.s3 import S3Client
from app.domain.errors import ExternalServiceError
from app.services.storage import StorageService
from app.settings import Settings
from tests import storage as storage_harness

OWNER = uuid.UUID("11111111-2222-3333-4444-555555555555")

KEY = "exports/watchlist/11111111-2222-3333-4444-555555555555/2024/03/01/090507-r-abcd1234.csv"
BODY = b"ticker,shares\nAAPL,10\n"


class TestRoundTrip:
    async def test_put_then_get_returns_the_same_bytes(self, s3_client: S3Client) -> None:
        await s3_client.put_object(KEY, BODY, content_type="text/csv; charset=utf-8")

        fetched = await s3_client.get_object(KEY)

        assert fetched.body == BODY
        assert fetched.key == KEY
        assert fetched.size_bytes == len(BODY)
        assert fetched.content_type == "text/csv; charset=utf-8"

    async def test_metadata_survives_the_round_trip(self, s3_client: S3Client) -> None:
        await s3_client.put_object(KEY, BODY, metadata={"owner": str(OWNER)})

        fetched = await s3_client.get_object(KEY)

        assert fetched.metadata == {"owner": str(OWNER)}

    async def test_an_empty_object_is_a_legitimate_object(self, s3_client: S3Client) -> None:
        """Zero bytes is a value, not an absence — `exists` must not confuse the two."""
        await s3_client.put_object(KEY, b"")

        assert await s3_client.object_exists(KEY) is True
        assert (await s3_client.get_object(KEY)).body == b""

    async def test_binary_bytes_survive_unchanged(self, s3_client: S3Client) -> None:
        payload = bytes(range(256))

        await s3_client.put_object(KEY, payload)

        assert (await s3_client.get_object(KEY)).body == payload

    async def test_a_second_put_replaces_the_first(self, s3_client: S3Client) -> None:
        await s3_client.put_object(KEY, b"first")
        await s3_client.put_object(KEY, b"second")

        assert (await s3_client.get_object(KEY)).body == b"second"

    async def test_the_etag_is_reported_with_its_quotes(self, s3_client: S3Client) -> None:
        info = await s3_client.put_object(KEY, BODY)

        assert info.etag is not None
        assert info.etag.startswith('"') and info.etag.endswith('"')


class TestExists:
    async def test_true_for_an_object_that_is_there(self, s3_client: S3Client) -> None:
        await s3_client.put_object(KEY, BODY)

        assert await s3_client.object_exists(KEY) is True

    async def test_false_for_one_that_is_not(self, s3_client: S3Client) -> None:
        assert await s3_client.object_exists("exports/nothing/here.csv") is False

    async def test_head_reports_the_length_and_type(self, s3_client: S3Client) -> None:
        await s3_client.put_object(KEY, BODY, content_type="text/csv; charset=utf-8")

        info = await s3_client.head_object(KEY)

        assert info.size_bytes == len(BODY)
        assert info.content_type == "text/csv; charset=utf-8"
        assert info.last_modified is not None
        assert info.last_modified.tzinfo is not None


class TestDelete:
    async def test_it_removes_the_object(self, s3_client: S3Client) -> None:
        await s3_client.put_object(KEY, BODY)

        await s3_client.delete_object(KEY)

        assert await s3_client.object_exists(KEY) is False

    async def test_deleting_something_that_is_not_there_succeeds(self, s3_client: S3Client) -> None:
        """S3's own semantics, and the reason `delete_object` returns nothing."""
        assert await s3_client.delete_object("exports/never/existed.csv") is None


class TestTheRealErrors:
    """What MinIO actually answers, rather than what the unit tier guessed it would."""

    async def test_a_missing_key_is_object_not_found(self, s3_client: S3Client) -> None:
        with pytest.raises(ExternalServiceError) as caught:
            await s3_client.get_object("exports/nothing/here.csv")

        assert caught.value.details["reason"] == "object_not_found"
        assert caught.value.details["status_code"] == 404
        assert "attempts" not in caught.value.details

    async def test_a_missing_bucket_is_distinguishable_on_a_get(
        self, s3_settings: Settings
    ) -> None:
        """A GET carries an error body, so `NoSuchBucket` survives — unlike a HEAD."""
        elsewhere = s3_settings.model_copy(
            update={"s3_bucket": storage_harness.unique_bucket_name()}
        )

        async with S3Client(elsewhere) as client:
            with pytest.raises(ExternalServiceError) as caught:
                await client.get_object(KEY)

        assert caught.value.details["reason"] == "bucket_not_found"

    async def test_a_missing_bucket_reads_as_a_missing_object_on_a_head(
        self, s3_settings: Settings
    ) -> None:
        """The documented caveat on `object_exists`, asserted rather than assumed."""
        elsewhere = s3_settings.model_copy(
            update={"s3_bucket": storage_harness.unique_bucket_name()}
        )

        async with S3Client(elsewhere) as client:
            assert await client.object_exists(KEY) is False

    async def test_a_wrong_secret_is_invalid_credentials_not_access_denied(
        self, s3_settings: Settings
    ) -> None:
        wrong = s3_settings.model_copy(
            update={"s3_secret_access_key": SecretStr("definitely-not-the-password")}
        )

        async with S3Client(wrong) as client:
            with pytest.raises(ExternalServiceError) as caught:
                await client.get_object(KEY)

        assert caught.value.details["reason"] == "invalid_credentials"

    async def test_an_unreachable_endpoint_is_a_transport_error(
        self, s3_settings: Settings
    ) -> None:
        """A closed port, not a wrong password — the fourth of the four distinctions."""
        nowhere = s3_settings.model_copy(update={"s3_endpoint_url": "http://127.0.0.1:1"})

        async with S3Client(nowhere) as client:
            with pytest.raises(ExternalServiceError) as caught:
                await client.get_object(KEY)

        assert caught.value.details["reason"] == "transport_error"
        assert "status_code" not in caught.value.details


class TestPresignedUrls:
    async def test_the_url_actually_retrieves_the_object(self, s3_client: S3Client) -> None:
        """The whole point: a signature MinIO produced is one MinIO accepts back."""
        await s3_client.put_object(KEY, BODY, content_type="text/csv; charset=utf-8")

        url = await s3_client.presigned_get_url(KEY, expires_in=300)

        async with httpx.AsyncClient(timeout=10.0) as fetch:
            response = await fetch.get(url)
        assert response.status_code == 200
        assert response.content == BODY

    async def test_it_needs_no_anvex_credential_which_is_why_it_is_one(
        self, s3_client: S3Client
    ) -> None:
        await s3_client.put_object(KEY, BODY)

        url = await s3_client.presigned_get_url(KEY, expires_in=300)

        assert "X-Amz-Signature" in url
        assert "X-Amz-Credential" in url

    async def test_an_unsigned_url_for_the_same_object_is_refused(
        self, s3_client: S3Client
    ) -> None:
        """Proves the bucket is private, so the signature is doing the work."""
        await s3_client.put_object(KEY, BODY)
        url = await s3_client.presigned_get_url(KEY, expires_in=300)

        async with httpx.AsyncClient(timeout=10.0) as fetch:
            response = await fetch.get(url.split("?")[0])

        assert response.status_code in (401, 403)

    async def test_a_filename_reaches_the_response_as_a_content_disposition(
        self, s3_client: S3Client
    ) -> None:
        await s3_client.put_object(KEY, BODY)

        url = await s3_client.presigned_get_url(KEY, expires_in=300, filename="my report.csv")

        async with httpx.AsyncClient(timeout=10.0) as fetch:
            response = await fetch.get(url)
        assert response.headers["content-disposition"] == 'attachment; filename="my report.csv"'

    async def test_it_signs_a_key_that_does_not_exist_and_the_url_404s(
        self, s3_client: S3Client
    ) -> None:
        """Presigning never contacts S3, which is why the service has to `HEAD` first."""
        url = await s3_client.presigned_get_url("exports/never/existed.csv", expires_in=300)

        async with httpx.AsyncClient(timeout=10.0) as fetch:
            response = await fetch.get(url)

        assert response.status_code == 404

    async def test_the_signed_url_is_written_to_no_log_line(self, s3_client: S3Client) -> None:
        """The one thing that must not be logged, asserted against a real signature."""
        await s3_client.put_object(KEY, BODY)

        with capture_logs() as entries:
            url = await s3_client.presigned_get_url(KEY, expires_in=300)

        rendered = repr(entries)
        assert url not in rendered
        assert "X-Amz-Signature" not in rendered
        assert "X-Amz-Credential" not in rendered


class TestTheSecretStaysHidden:
    async def test_it_appears_in_no_log_line_on_a_real_round_trip(
        self, s3_client: S3Client, s3_settings: Settings
    ) -> None:
        secret = s3_settings.s3_secret_access_key.get_secret_value()

        with capture_logs() as entries:
            await s3_client.put_object(KEY, BODY)
            await s3_client.get_object(KEY)
            await s3_client.delete_object(KEY)

        assert entries  # the sweep is not vacuous
        assert secret not in repr(entries)

    async def test_it_appears_in_no_log_line_when_the_server_rejects_it(
        self, s3_settings: Settings
    ) -> None:
        secret = "a-wrong-but-recognisable-secret"
        wrong = s3_settings.model_copy(update={"s3_secret_access_key": SecretStr(secret)})

        with capture_logs() as entries:
            async with S3Client(wrong) as client:
                with pytest.raises(ExternalServiceError) as caught:
                    await client.get_object(KEY)

        assert secret not in repr(entries)
        assert secret not in str(caught.value)
        assert secret not in str(caught.value.details)


class TestTheStorageServiceEndToEnd:
    """The service's own path, against a real bucket rather than the in-memory fake."""

    async def test_store_then_read_then_delete(
        self, s3_client: S3Client, s3_settings: Settings
    ) -> None:
        service = StorageService(s3_settings, client=s3_client)

        stored = await service.store_export(
            owner_id=OWNER, resource="watchlist", name="Q1 Report", extension="csv", body=BODY
        )

        assert await service.read_export(owner_id=OWNER, key=stored.key) == BODY
        assert await service.export_exists(owner_id=OWNER, key=stored.key) is True
        await service.delete_export(owner_id=OWNER, key=stored.key)
        assert await service.export_exists(owner_id=OWNER, key=stored.key) is False

    async def test_the_content_type_the_domain_chose_is_the_one_s3_serves_back(
        self, s3_client: S3Client, s3_settings: Settings
    ) -> None:
        service = StorageService(s3_settings, client=s3_client)

        stored = await service.store_export(
            owner_id=OWNER, resource="watchlist", name="r", extension="csv", body=BODY
        )

        assert (await s3_client.head_object(stored.key)).content_type == ("text/csv; charset=utf-8")

    async def test_a_download_url_from_the_service_retrieves_the_export(
        self, s3_client: S3Client, s3_settings: Settings
    ) -> None:
        service = StorageService(s3_settings, client=s3_client)
        stored = await service.store_export(
            owner_id=OWNER, resource="watchlist", name="r", extension="csv", body=BODY
        )

        url = await service.download_url(owner_id=OWNER, key=stored.key)

        async with httpx.AsyncClient(timeout=10.0) as fetch:
            response = await fetch.get(url)
        assert response.status_code == 200
        assert response.content == BODY


class TestTheHarnessItself:
    def test_the_test_endpoint_is_localhost_and_can_never_be_aws(self) -> None:
        endpoint = storage_harness.endpoint_url()

        assert endpoint.startswith("http://")
        assert "amazonaws.com" not in endpoint

    def test_each_test_gets_its_own_bucket(self, s3_bucket: str) -> None:
        assert s3_bucket.startswith(storage_harness.BUCKET_PREFIX)

    def test_the_settings_the_tier_uses_point_at_that_bucket(
        self, s3_bucket: str, s3_settings: Settings
    ) -> None:
        assert s3_settings.s3_bucket == s3_bucket
        assert s3_settings.s3_endpoint_url == storage_harness.endpoint_url()
