"""Unit tests for ``app/services/storage.py``, against :class:`tests.helpers.FakeS3Client`.

No bucket, no ``botocore``, no event loop belonging to anything but pytest. The service's own
decisions are what is under test: the key it composes, the content type it attaches, the
ownership gate every use case goes through, and the single translation of ``object_not_found``
into a 404 while every other S3 failure stays a 502.

The ownership sweep at the bottom derives its case list from ``vars(StorageService)`` and
asserts the list is complete, so a use case added without an isolation test fails the suite
rather than quietly going unchecked (``CLAUDE.md`` §4, ANV-15's rule).
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from app.domain.errors import ExternalServiceError, NotFoundError
from app.domain.storage import (
    DEFAULT_DOWNLOAD_TTL,
    MAX_DOWNLOAD_TTL,
    export_key,
    export_prefix_for_owner,
)
from app.services.storage import RESOURCE, StorageService, StoredExport
from app.settings import Settings
from tests.helpers import FakeS3Client

OWNER = uuid.UUID("11111111-2222-3333-4444-555555555555")
INTRUDER = uuid.UUID("99999999-8888-7777-6666-555555555555")

NOW = datetime(2024, 3, 1, 9, 5, 7, tzinfo=UTC)

OWNED_KEY = export_key(
    resource="watchlist", owner_id=OWNER, name="report", extension="csv", now=NOW, unique="abcd1234"
)
FOREIGN_KEY = export_key(
    resource="watchlist",
    owner_id=INTRUDER,
    name="report",
    extension="csv",
    now=NOW,
    unique="abcd1234",
)

#: Use cases that legitimately have no key to check ownership of, because they *make* one.
UNGATED = frozenset({"store_export"})


def settings() -> Settings:
    return Settings(
        s3_endpoint_url="http://minio:9000",
        s3_access_key_id="k",
        s3_secret_access_key=SecretStr("s"),
        s3_bucket="unit-test-bucket",
    )


def build(
    objects: dict[str, bytes] | None = None, *, error: Exception | None = None
) -> tuple[StorageService, FakeS3Client]:
    client = FakeS3Client(objects, error=error)
    return StorageService(settings(), client=client), client


class TestStoreExport:
    async def test_it_writes_under_the_owner_s_prefix(self) -> None:
        service, client = build()

        stored = await service.store_export(
            owner_id=OWNER, resource="watchlist", name="Q1 Report", extension="csv", body=b"a,b\n"
        )

        assert isinstance(stored, StoredExport)
        assert stored.key.startswith(export_prefix_for_owner(resource="watchlist", owner_id=OWNER))
        assert client.objects[stored.key] == b"a,b\n"

    async def test_the_content_type_comes_from_the_domain_rule(self) -> None:
        service, client = build()

        stored = await service.store_export(
            owner_id=OWNER, resource="watchlist", name="r", extension="csv", body=b"x"
        )

        assert stored.content_type == "text/csv; charset=utf-8"
        assert client.content_types[stored.key] == "text/csv; charset=utf-8"

    async def test_an_unknown_extension_downloads_rather_than_being_guessed(self) -> None:
        service, _ = build()

        stored = await service.store_export(
            owner_id=OWNER, resource="watchlist", name="r", extension="dat", body=b"x"
        )

        assert stored.content_type == "application/octet-stream"

    async def test_the_key_is_readable_and_carries_the_slugified_name(self) -> None:
        service, _ = build()

        stored = await service.store_export(
            owner_id=OWNER, resource="stock-data", name="AAPL / 2024", extension="json", body=b"{}"
        )

        assert "aapl-2024" in stored.key
        assert stored.key.endswith(".json")

    async def test_two_exports_of_the_same_name_are_two_objects(self) -> None:
        """`PutObject` has no "fail if exists"; a collision would be a silent data loss."""
        service, client = build()
        common = {
            "owner_id": OWNER,
            "resource": "watchlist",
            "name": "report",
            "extension": "csv",
        }

        first = await service.store_export(**common, body=b"one")
        second = await service.store_export(**common, body=b"two")

        assert first.key != second.key
        assert len(client.objects) == 2

    async def test_the_result_reports_what_was_written(self) -> None:
        service, _ = build()

        stored = await service.store_export(
            owner_id=OWNER, resource="watchlist", name="r", extension="csv", body=b"12345"
        )

        assert stored.size_bytes == 5
        assert stored.etag == '"fake-etag"'

    async def test_an_unusable_resource_fails_before_anything_is_sent(self) -> None:
        service, client = build()

        with pytest.raises(ValueError, match="resource segment"):
            await service.store_export(
                owner_id=OWNER, resource="Not A Resource", name="r", extension="csv", body=b"x"
            )

        assert client.calls == []

    async def test_the_key_the_service_builds_is_the_key_the_domain_would_build(self) -> None:
        """Composed, never re-spelled: the layout lives in one function."""
        service, _ = build()

        stored = await service.store_export(
            owner_id=OWNER, resource="watchlist", name="report", extension="csv", body=b"x"
        )
        unique = stored.key.rsplit("-", 1)[-1].removesuffix(".csv")
        day, stamp = stored.key.split("/")[3:6], stored.key.split("/")[6][:6]

        assert stored.key == export_key(
            resource="watchlist",
            owner_id=OWNER,
            name="report",
            extension="csv",
            now=datetime(
                int(day[0]),
                int(day[1]),
                int(day[2]),
                int(stamp[0:2]),
                int(stamp[2:4]),
                int(stamp[4:6]),
                tzinfo=UTC,
            ),
            unique=unique,
        )


class TestReadExport:
    async def test_it_returns_the_bytes(self) -> None:
        service, _ = build({OWNED_KEY: b"a,b\n1,2\n"})

        assert await service.read_export(owner_id=OWNER, key=OWNED_KEY) == b"a,b\n1,2\n"

    async def test_a_missing_object_is_a_404_not_a_502(self) -> None:
        """The one S3 failure that is an absence rather than an outage."""
        service, _ = build()

        with pytest.raises(NotFoundError) as caught:
            await service.read_export(owner_id=OWNER, key=OWNED_KEY)

        assert caught.value.details["resource"] == RESOURCE

    async def test_every_other_s3_failure_stays_a_502(self) -> None:
        outage = ExternalServiceError("s3", details={"reason": "transport_error"})
        service, _ = build({OWNED_KEY: b"x"}, error=outage)

        with pytest.raises(ExternalServiceError) as caught:
            await service.read_export(owner_id=OWNER, key=OWNED_KEY)

        assert caught.value.details["reason"] == "transport_error"

    async def test_the_translation_keys_on_the_reason_not_the_message(self) -> None:
        """A message-matching translation would break the first time a string is reworded."""
        misleading = ExternalServiceError(
            "s3", "The upstream service 's3' has no such object.", details={"reason": "sdk_error"}
        )
        service, _ = build({OWNED_KEY: b"x"}, error=misleading)

        with pytest.raises(ExternalServiceError):
            await service.read_export(owner_id=OWNER, key=OWNED_KEY)


class TestExportExists:
    async def test_true_for_an_owned_object_that_is_there(self) -> None:
        service, _ = build({OWNED_KEY: b"x"})

        assert await service.export_exists(owner_id=OWNER, key=OWNED_KEY) is True

    async def test_false_for_an_owned_object_that_is_gone(self) -> None:
        service, _ = build()

        assert await service.export_exists(owner_id=OWNER, key=OWNED_KEY) is False

    async def test_a_foreign_key_answers_false_identically_to_an_absent_one(self) -> None:
        """The one use case whose normal answer is already a boolean, so it stays one."""
        service, client = build({FOREIGN_KEY: b"x"})

        assert await service.export_exists(owner_id=OWNER, key=FOREIGN_KEY) is False
        assert client.calls == []


class TestDeleteExport:
    async def test_it_removes_the_object(self) -> None:
        service, client = build({OWNED_KEY: b"x"})

        await service.delete_export(owner_id=OWNER, key=OWNED_KEY)

        assert client.objects == {}

    async def test_deleting_something_already_gone_succeeds(self) -> None:
        """S3's own semantics; a truthful boolean would need a HEAD and still be a race."""
        service, _ = build()

        assert await service.delete_export(owner_id=OWNER, key=OWNED_KEY) is None


class TestDownloadUrl:
    async def test_it_returns_the_signed_url(self) -> None:
        service, client = build({OWNED_KEY: b"x"})

        url = await service.download_url(owner_id=OWNER, key=OWNED_KEY)

        assert url == client.presigned_url

    async def test_the_default_ttl_comes_from_the_domain_rule(self) -> None:
        service, client = build({OWNED_KEY: b"x"})

        await service.download_url(owner_id=OWNER, key=OWNED_KEY)

        presign = next(kwargs for name, kwargs in client.calls if name == "presigned_get_url")
        assert presign["expires_in"] == int(DEFAULT_DOWNLOAD_TTL.total_seconds())

    async def test_an_over_long_ttl_is_clamped_because_a_signature_is_a_blast_radius(
        self,
    ) -> None:
        service, client = build({OWNED_KEY: b"x"})

        await service.download_url(owner_id=OWNER, key=OWNED_KEY, ttl=timedelta(days=30))

        presign = next(kwargs for name, kwargs in client.calls if name == "presigned_get_url")
        assert presign["expires_in"] == int(MAX_DOWNLOAD_TTL.total_seconds())

    async def test_it_checks_the_object_exists_first(self) -> None:
        """Presigning never contacts S3, so it would happily sign a key deleted last week."""
        service, client = build()

        with pytest.raises(NotFoundError):
            await service.download_url(owner_id=OWNER, key=OWNED_KEY)

        assert client.operations == ["object_exists"]


# ---------------------------------------------------------------------------------------
# The ownership gate — one parameterised sweep, derived from the service's own surface
# ---------------------------------------------------------------------------------------


def use_cases() -> list[str]:
    """Every public ``async`` method on the service, read off the class."""
    return sorted(
        name
        for name, value in vars(StorageService).items()
        if not name.startswith("_") and inspect.iscoroutinefunction(value)
    )


async def invoke(service: StorageService, name: str, *, owner_id: uuid.UUID, key: str) -> object:
    """Call one gated use case. All four take exactly ``owner_id`` and ``key``."""
    return await getattr(service, name)(owner_id=owner_id, key=key)


async def assert_refused(
    service: StorageService, name: str, *, owner_id: uuid.UUID, key: str
) -> None:
    """Assert one use case refused, in whichever way that use case refuses.

    ``export_exists`` answers ``False`` rather than raising, and deliberately so — its normal
    answer is already a boolean, so raising for a foreign key would make "not yours" and "not
    there" distinguishable. Every other use case raises the 404. Spelling that exception out
    here keeps it a *stated* exception rather than a hole in the sweep.
    """
    if name == "export_exists":
        assert await invoke(service, name, owner_id=owner_id, key=key) is False
        return
    with pytest.raises(NotFoundError):
        await invoke(service, name, owner_id=owner_id, key=key)


class TestTheOwnershipGate:
    def test_the_sweep_covers_every_use_case(self) -> None:
        """The list is derived and asserted complete, so a new method cannot skip the gate."""
        assert set(use_cases()) == UNGATED | {
            "read_export",
            "export_exists",
            "delete_export",
            "download_url",
        }

    @pytest.mark.parametrize("use_case", sorted(set(use_cases()) - UNGATED - {"export_exists"}))
    async def test_a_foreign_key_is_refused(self, use_case: str) -> None:
        service, _ = build({FOREIGN_KEY: b"x"})

        with pytest.raises(NotFoundError):
            await invoke(service, use_case, owner_id=OWNER, key=FOREIGN_KEY)

    @pytest.mark.parametrize("use_case", sorted(set(use_cases()) - UNGATED - {"export_exists"}))
    async def test_the_refusal_is_byte_identical_to_one_for_a_key_that_never_existed(
        self, use_case: str
    ) -> None:
        """``CLAUDE.md`` §4: a 404, and the same 404, or the response is an existence oracle."""
        service, _ = build({FOREIGN_KEY: b"x"})
        never_existed = export_key(
            resource="watchlist",
            owner_id=INTRUDER,
            name="nothing",
            extension="csv",
            now=NOW,
            unique="00000000",
        )

        with pytest.raises(NotFoundError) as foreign:
            await invoke(service, use_case, owner_id=OWNER, key=FOREIGN_KEY)
        with pytest.raises(NotFoundError) as absent:
            await invoke(service, use_case, owner_id=OWNER, key=never_existed)

        assert foreign.value.code == absent.value.code == "not_found"
        assert set(foreign.value.details) == set(absent.value.details)
        assert foreign.value.details["resource"] == absent.value.details["resource"] == RESOURCE

    @pytest.mark.parametrize("use_case", sorted(set(use_cases()) - UNGATED))
    async def test_the_refusal_reaches_the_bucket_not_at_all(self, use_case: str) -> None:
        """No I/O, so response *time* does not answer the question either."""
        service, client = build({FOREIGN_KEY: b"x"})

        await assert_refused(service, use_case, owner_id=OWNER, key=FOREIGN_KEY)

        assert client.calls == []

    @pytest.mark.parametrize("use_case", sorted(set(use_cases()) - UNGATED))
    async def test_a_key_that_is_not_an_export_key_at_all_is_refused(self, use_case: str) -> None:
        """A caller cannot reach outside `exports/` by handing over a raw key."""
        service, client = build({"secrets/root.pem": b"x"})

        await assert_refused(service, use_case, owner_id=OWNER, key="secrets/root.pem")

        assert client.calls == []


class TestTheServiceShape:
    def test_it_takes_no_session_because_it_persists_nothing(self) -> None:
        parameters = inspect.signature(StorageService.__init__).parameters

        assert "session" not in parameters
        assert parameters["client"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_the_client_is_required_because_it_owns_a_lifetime(self) -> None:
        assert inspect.signature(StorageService.__init__).parameters["client"].default is (
            inspect.Parameter.empty
        )

    def test_every_use_case_takes_keyword_only_arguments(self) -> None:
        for name in use_cases():
            parameters = list(inspect.signature(getattr(StorageService, name)).parameters.values())
            assert all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                for parameter in parameters[1:]  # skip `self`
            ), name


class TestTheDependencyWiring:
    """``app/deps/storage.py`` decides nothing about behaviour, but it does own the client's
    lifetime — and a leaked connection pool is exactly what nothing else would notice.
    """

    async def test_the_client_factory_closes_what_it_opened(self) -> None:
        from app.deps.storage import get_s3_client

        generator = get_s3_client(settings())
        client = await anext(generator)

        assert client.is_closed is False
        with pytest.raises(StopAsyncIteration):
            await anext(generator)
        assert client.is_closed is True

    async def test_constructing_one_opens_no_socket(self) -> None:
        """Why per-request construction is affordable: the SDK client is lazy."""
        from app.deps.storage import get_s3_client

        generator = get_s3_client(settings())
        client = await anext(generator)

        assert client._client is None
        await generator.aclose()

    def test_the_service_factory_wires_the_two_collaborators(self) -> None:
        from app.clients.s3 import S3Client
        from app.deps.storage import get_storage_service

        configured = settings()
        client = S3Client(configured)

        service = get_storage_service(configured, client)

        assert isinstance(service, StorageService)
        assert service.client is client
        assert service.settings is configured

    def test_a_service_is_not_constructible_without_a_client(self) -> None:
        """Keyword-only and required, unlike a repo: a client owns a lifetime, so there is
        no module-level singleton to default to."""
        with pytest.raises(TypeError):
            StorageService(settings())  # type: ignore[call-arg]

    def test_the_expensive_shared_part_is_shared_and_the_loop_bound_part_is_not(self) -> None:
        """The client-lifetime decision, asserted: one `Session` process-wide, one client
        per request. See ``app/deps/storage.py`` for why the halves are split there."""
        from app.clients.s3 import S3Client, default_session

        first, second = S3Client(settings()), S3Client(settings())

        assert first is not second
        assert first._session is second._session is default_session()
