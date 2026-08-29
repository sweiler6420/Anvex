"""Unit tests for ``app/clients/s3.py`` — the SDK patched out, no network, no MinIO.

Everything here runs with Docker stopped, because everything here is about *translation*:
which ``botocore`` exception becomes which ``details["reason"]``, what a presign request is
assembled out of, what is and is not written to a log line. The round trip against a real
bucket is ``tests/integration/test_client_s3.py``.

The fake SDK below is deliberately shaped like the real one — ``session.client()`` is an
async context manager, ``get_object`` answers a mapping whose ``Body`` is an async context
manager with an ``await read()`` — because a fake that is easier to use than the real thing
tests the fake.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import aioboto3
import botocore.exceptions as boto_errors
import pytest
from botocore.config import Config
from pydantic import SecretStr, ValidationError
from structlog.testing import capture_logs

from app.clients import s3 as s3_module
from app.clients.base import REDACTED, Failure
from app.clients.s3 import (
    NOT_CONFIGURED,
    RETRY_ATTEMPTS,
    SERVICE_NAME,
    S3Client,
    S3Failure,
    S3Object,
    S3ObjectInfo,
    classify,
    default_session,
)
from app.domain.errors import ExternalServiceError
from app.settings import Settings

SECRET = "unit-test-s3-secret-value"
ACCESS_KEY = "unit-test-access-key"
BUCKET = "unit-test-bucket"
KEY = "exports/watchlist/11111111-2222-3333-4444-555555555555/2024/03/01/090507-r-abcd1234.csv"


def settings(**overrides: Any) -> Settings:
    """Settings with every S3 field pinned, so no test depends on the developer's `.env`."""
    return Settings(
        **{
            "s3_endpoint_url": "http://minio:9000",
            "s3_region": "us-east-1",
            "s3_access_key_id": ACCESS_KEY,
            "s3_secret_access_key": SecretStr(SECRET),
            "s3_bucket": BUCKET,
            **overrides,
        }
    )


# ---------------------------------------------------------------------------------------
# The fake SDK
# ---------------------------------------------------------------------------------------


class FakeBody:
    """``response["Body"]`` — an async context manager over some bytes."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.closed = False

    async def __aenter__(self) -> FakeBody:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.closed = True

    async def read(self) -> bytes:
        return self.payload


class FakeSdkClient:
    """What ``aioboto3``'s ``session.client("s3")`` yields, with the five calls we make."""

    def __init__(
        self,
        *,
        error: BaseException | None = None,
        body: bytes = b"id,name\n",
        response: dict[str, Any] | None = None,
        url: str = "https://s3.example.com/b/k?X-Amz-Signature=deadbeefcafe",
    ) -> None:
        self.error = error
        self.body = body
        self.response = response or {}
        self.url = url
        #: ``(operation, kwargs)`` for every call, in order.
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self._record("put_object", kwargs)
        return {"ETag": '"abc123"', **self.response}

    async def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self._record("get_object", kwargs)
        return {
            "ETag": '"abc123"',
            "ContentType": "text/csv; charset=utf-8",
            "Metadata": {"owner": "x"},
            "Body": FakeBody(self.body),
            **self.response,
        }

    async def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self._record("head_object", kwargs)
        return {"ETag": '"abc123"', "ContentLength": len(self.body), **self.response}

    async def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        self._record("delete_object", kwargs)
        return {}

    async def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
        self._record("generate_presigned_url", {"operation": operation, **kwargs})
        return self.url

    @property
    def operations(self) -> list[str]:
        return [operation for operation, _ in self.calls]

    def _record(self, operation: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((operation, kwargs))
        if self.error is not None:
            raise self.error


class FakeSession:
    """A stand-in for :class:`aioboto3.Session` that records how the client was built."""

    def __init__(self, sdk: FakeSdkClient | None = None) -> None:
        self.sdk = sdk or FakeSdkClient()
        self.service_name: str | None = None
        self.kwargs: dict[str, Any] = {}
        self.entered = 0
        self.exited = 0

    def client(self, service_name: str, **kwargs: Any) -> _FakeClientContext:
        self.service_name = service_name
        self.kwargs = kwargs
        return _FakeClientContext(self)


class _FakeClientContext:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSdkClient:
        self.session.entered += 1
        return self.session.sdk

    async def __aexit__(self, *exc_info: object) -> bool:
        self.session.exited += 1
        return False


def build(
    sdk: FakeSdkClient | None = None, **overrides: Any
) -> tuple[S3Client, FakeSession, FakeSdkClient]:
    """A client wired to a fake session, plus the two fakes so a test can assert on them."""
    session = FakeSession(sdk)
    client = S3Client(settings(**overrides), session=session)
    return client, session, session.sdk


def client_error(
    code: str | None, status: int | None = None, operation: str = "GetObject"
) -> boto_errors.ClientError:
    """A ``ClientError`` shaped exactly as ``botocore`` builds one."""
    response: dict[str, Any] = {"Error": {"Message": "something went wrong"}}
    if code is not None:
        response["Error"]["Code"] = code
    if status is not None:
        response["ResponseMetadata"] = {"HTTPStatusCode": status}
    return boto_errors.ClientError(response, operation)


# ---------------------------------------------------------------------------------------
# The failure taxonomy
# ---------------------------------------------------------------------------------------


class TestTheFailureEnum:
    """Why this is not :class:`~app.clients.base.Failure` — asserted, not left to prose."""

    def test_every_member_has_a_message(self) -> None:
        assert set(s3_module._MESSAGES) == set(S3Failure)

    def test_no_message_leaks_a_configured_value(self) -> None:
        """These strings are a public API contract (``CLAUDE.md`` §4)."""
        for message in s3_module._MESSAGES.values():
            assert "{vendor}" in message
            assert BUCKET not in message
            assert "http" not in message
            assert "exports/" not in message

    def test_the_four_shared_reasons_are_spelled_exactly_as_the_http_ones(self) -> None:
        """One vocabulary across `app/clients/`, so a consumer learns `reason` once."""
        assert S3Failure.TRANSPORT.value == Failure.TRANSPORT.value
        assert S3Failure.SERVER_ERROR.value == Failure.SERVER_ERROR.value
        assert S3Failure.RATE_LIMITED.value == Failure.RATE_LIMITED.value
        assert S3Failure.CLIENT_ERROR.value == Failure.CLIENT_ERROR.value

    def test_the_distinctions_the_http_enum_cannot_make(self) -> None:
        """The reason for a separate enum: these five have no HTTP twin."""
        s3_only = {member.value for member in S3Failure} - {member.value for member in Failure}

        assert s3_only == {
            "object_not_found",
            "bucket_not_found",
            "invalid_credentials",
            "access_denied",
            "sdk_error",
        }

    def test_it_does_not_inherit_a_member_s3_cannot_produce(self) -> None:
        values = {member.value for member in S3Failure}

        assert "unexpected_redirect" not in values
        assert "malformed_response" not in values


class TestClassify:
    """The mapping the whole module rests on, tested with no client at all."""

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("NoSuchKey", S3Failure.NOT_FOUND),
            ("NotFound", S3Failure.NOT_FOUND),
            ("404", S3Failure.NOT_FOUND),
            ("NoSuchBucket", S3Failure.BUCKET_NOT_FOUND),
            ("AccessDenied", S3Failure.ACCESS_DENIED),
            ("403", S3Failure.ACCESS_DENIED),
            ("InvalidAccessKeyId", S3Failure.INVALID_CREDENTIALS),
            ("SignatureDoesNotMatch", S3Failure.INVALID_CREDENTIALS),
            ("ExpiredToken", S3Failure.INVALID_CREDENTIALS),
            ("SlowDown", S3Failure.RATE_LIMITED),
            ("InternalError", S3Failure.SERVER_ERROR),
        ],
    )
    def test_a_code_that_names_itself_is_believed(self, code: str, expected: S3Failure) -> None:
        failure, reported, _ = classify(client_error(code))

        assert failure is expected
        assert reported == code

    def test_the_four_things_the_ticket_asks_to_be_distinguishable_are(self) -> None:
        """A missing key, a missing bucket, bad credentials and a dead socket."""
        reasons = {
            classify(client_error("NoSuchKey", 404))[0],
            classify(client_error("NoSuchBucket", 404))[0],
            classify(boto_errors.NoCredentialsError())[0],
            classify(boto_errors.EndpointConnectionError(endpoint_url="http://x"))[0],
        }

        assert len(reasons) == 4

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (429, S3Failure.RATE_LIMITED),
            (500, S3Failure.SERVER_ERROR),
            (502, S3Failure.SERVER_ERROR),
            (404, S3Failure.NOT_FOUND),
            (403, S3Failure.ACCESS_DENIED),
            (400, S3Failure.CLIENT_ERROR),
            (412, S3Failure.CLIENT_ERROR),
            (302, S3Failure.SDK_ERROR),
        ],
    )
    def test_an_unknown_code_falls_back_to_the_status_line(
        self, status: int, expected: S3Failure
    ) -> None:
        failure, code, reported_status = classify(client_error("SomethingNewAws", status))

        assert failure is expected
        assert code == "SomethingNewAws"
        assert reported_status == status

    def test_no_code_and_no_status_is_the_sdk_objecting(self) -> None:
        failure, code, status = classify(client_error(None))

        assert (failure, code, status) == (S3Failure.SDK_ERROR, None, None)

    @pytest.mark.parametrize(
        "error",
        [
            boto_errors.NoCredentialsError(),
            boto_errors.PartialCredentialsError(provider="env", cred_var="secret_key"),
        ],
    )
    def test_a_credential_failure_is_not_a_transport_failure(self, error: BaseException) -> None:
        failure, code, status = classify(error)

        assert failure is S3Failure.INVALID_CREDENTIALS
        assert (code, status) == (None, None)

    @pytest.mark.parametrize(
        "error",
        [
            boto_errors.EndpointConnectionError(endpoint_url="http://minio:9000"),
            boto_errors.ConnectTimeoutError(endpoint_url="http://minio:9000"),
            boto_errors.ReadTimeoutError(endpoint_url="http://minio:9000"),
            boto_errors.ConnectionClosedError(endpoint_url="http://minio:9000"),
        ],
    )
    def test_the_whole_network_family_is_one_reason(self, error: BaseException) -> None:
        assert classify(error)[0] is S3Failure.TRANSPORT

    @pytest.mark.parametrize(
        "error",
        [
            boto_errors.ParamValidationError(report="bad"),
            boto_errors.BotoCoreError(),
            RuntimeError("something nobody anticipated"),
        ],
    )
    def test_anything_else_is_the_sdk_and_never_escapes(self, error: BaseException) -> None:
        assert classify(error)[0] is S3Failure.SDK_ERROR


class TestTheErrorItRaises:
    def test_details_carry_the_reason_and_the_status(self) -> None:
        client, _, _ = build()

        error = client._error(S3Failure.NOT_FOUND, status_code=404)

        assert error.details == {"service": "s3", "reason": "object_not_found", "status_code": 404}

    def test_there_is_no_attempts_key_because_botocore_owns_the_retry_loop(self) -> None:
        """Inventing a `1` would be worse than an absent key (ANV-18's rule)."""
        client, _, _ = build()

        assert "attempts" not in client._error(S3Failure.SERVER_ERROR, status_code=500).details

    def test_status_code_is_absent_rather_than_null_when_nothing_reported_one(self) -> None:
        client, _, _ = build()

        assert client._error(S3Failure.TRANSPORT).details == {
            "service": "s3",
            "reason": "transport_error",
        }

    def test_details_never_carry_the_bucket_or_the_key(self) -> None:
        """The error body is public and an export key contains its owner's UUID."""
        client, _, _ = build()

        rendered = str(client._error(S3Failure.NOT_FOUND, status_code=404).details)

        assert BUCKET not in rendered
        assert "exports/" not in rendered

    def test_it_is_the_layer_s_one_exit(self) -> None:
        client, _, _ = build()

        assert isinstance(client._error(S3Failure.SDK_ERROR), ExternalServiceError)


# ---------------------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------------------


class TestConfiguration:
    def test_the_service_is_s3_and_the_bucket_comes_from_settings(self) -> None:
        client, _, _ = build()

        assert client.vendor == "s3"
        assert client.bucket == BUCKET

    async def test_credentials_are_passed_explicitly_never_left_to_the_default_chain(
        self,
    ) -> None:
        """The guard against a blank config quietly authenticating against real AWS."""
        client, session, _ = build()

        await client.head_object(KEY)

        assert session.service_name == SERVICE_NAME
        assert session.kwargs["aws_access_key_id"] == ACCESS_KEY
        assert session.kwargs["aws_secret_access_key"] == SECRET
        assert session.kwargs["endpoint_url"] == "http://minio:9000"
        assert session.kwargs["region_name"] == "us-east-1"

    async def test_a_custom_endpoint_pins_path_style_addressing(self) -> None:
        """`auto` would build `http://bucket.localhost:9000`, which resolves nowhere."""
        client, session, _ = build()

        await client.head_object(KEY)

        config: Config = session.kwargs["config"]
        assert config.s3 == {"addressing_style": "path"}

    async def test_no_endpoint_means_real_aws_and_its_own_addressing(self) -> None:
        """The one setting that is the entire deploy-time difference."""
        client, session, _ = build(s3_endpoint_url=None)

        await client.head_object(KEY)

        assert session.kwargs["endpoint_url"] is None
        assert session.kwargs["config"].s3 == {"addressing_style": "auto"}

    async def test_the_retry_loop_is_the_sdk_s(self) -> None:
        client, session, _ = build()

        await client.head_object(KEY)

        assert session.kwargs["config"].retries == {
            "max_attempts": RETRY_ATTEMPTS,
            "mode": "standard",
        }

    async def test_connect_and_read_timeouts_are_two_named_numbers(self) -> None:
        client, session, _ = build()

        await client.head_object(KEY)

        config: Config = session.kwargs["config"]
        assert config.connect_timeout == s3_module.CONNECT_TIMEOUT_SECONDS
        assert config.read_timeout == s3_module.READ_TIMEOUT_SECONDS
        assert config.connect_timeout != config.read_timeout


class TestNotConfigured:
    @pytest.mark.parametrize(
        ("override", "setting"),
        [
            ({"s3_bucket": "   "}, "S3_BUCKET"),
            ({"s3_access_key_id": ""}, "S3_ACCESS_KEY_ID"),
            ({"s3_secret_access_key": SecretStr("")}, "S3_SECRET_ACCESS_KEY"),
        ],
    )
    async def test_a_blank_setting_is_refused_before_any_sdk_call(
        self, override: dict[str, Any], setting: str
    ) -> None:
        client, session, sdk = build(**override)

        with pytest.raises(ExternalServiceError) as caught:
            await client.put_object(KEY, b"x")

        assert caught.value.details == {
            "service": "s3",
            "reason": NOT_CONFIGURED,
            "setting": setting,
        }
        # Nothing was constructed and nothing was called: the point of a pre-flight.
        assert session.entered == 0
        assert sdk.calls == []

    async def test_it_is_not_raised_through_error_because_no_call_was_made(self) -> None:
        """Every `S3Failure` describes how a *call* went wrong; this one had none."""
        client, _, _ = build(s3_bucket="")

        with pytest.raises(ExternalServiceError) as caught:
            await client.object_exists(KEY)

        assert caught.value.details["reason"] not in {member.value for member in S3Failure}

    def test_is_configured_reports_the_same_thing_without_provoking_a_failure(self) -> None:
        assert build()[0].is_configured is True
        assert build(s3_secret_access_key=SecretStr(" "))[0].is_configured is False

    @pytest.mark.parametrize(
        "operation",
        ["put_object", "get_object", "head_object", "object_exists", "delete_object"],
    )
    async def test_every_operation_goes_through_the_pre_flight(self, operation: str) -> None:
        """A new operation cannot forget it: they all route through `_run`."""
        client, _, sdk = build(s3_bucket="")

        with pytest.raises(ExternalServiceError, match="not configured"):
            call = getattr(client, operation)
            await (call(KEY, b"x") if operation == "put_object" else call(KEY))

        assert sdk.calls == []


# ---------------------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------------------


class TestPutObject:
    async def test_it_sends_the_bucket_key_body_and_content_type(self) -> None:
        client, _, sdk = build()

        info = await client.put_object(KEY, b"id,name\n", content_type="text/csv")

        assert sdk.calls == [
            (
                "put_object",
                {
                    "Bucket": BUCKET,
                    "Key": KEY,
                    "Body": b"id,name\n",
                    "ContentType": "text/csv",
                },
            )
        ]
        assert isinstance(info, S3ObjectInfo)
        assert (info.key, info.size_bytes, info.etag) == (KEY, 8, '"abc123"')

    async def test_content_type_and_metadata_are_omitted_rather_than_sent_empty(self) -> None:
        client, _, sdk = build()

        await client.put_object(KEY, b"x")

        assert set(sdk.calls[0][1]) == {"Bucket", "Key", "Body"}

    async def test_metadata_is_forwarded_when_given(self) -> None:
        client, _, sdk = build()

        await client.put_object(KEY, b"x", metadata={"owner": "abc"})

        assert sdk.calls[0][1]["Metadata"] == {"owner": "abc"}


class TestGetObject:
    async def test_it_reads_the_whole_body_and_closes_the_stream(self) -> None:
        sdk = FakeSdkClient(body=b"a,b\n1,2\n")
        client, _, _ = build(sdk)

        obj = await client.get_object(KEY)

        assert isinstance(obj, S3Object)
        assert obj.body == b"a,b\n1,2\n"
        assert obj.size_bytes == 8
        assert obj.content_type == "text/csv; charset=utf-8"
        assert obj.metadata == {"owner": "x"}

    async def test_a_missing_key_is_object_not_found(self) -> None:
        client, _, _ = build(FakeSdkClient(error=client_error("NoSuchKey", 404)))

        with pytest.raises(ExternalServiceError) as caught:
            await client.get_object(KEY)

        assert caught.value.details["reason"] == "object_not_found"

    async def test_a_missing_bucket_says_so_because_a_get_carries_an_error_body(self) -> None:
        client, _, _ = build(FakeSdkClient(error=client_error("NoSuchBucket", 404)))

        with pytest.raises(ExternalServiceError) as caught:
            await client.get_object(KEY)

        assert caught.value.details["reason"] == "bucket_not_found"

    async def test_no_botocore_exception_escapes(self) -> None:
        client, _, _ = build(FakeSdkClient(error=boto_errors.ParamValidationError(report="no")))

        with pytest.raises(ExternalServiceError):
            await client.get_object(KEY)


class TestObjectExists:
    async def test_true_when_the_head_succeeds(self) -> None:
        client, _, sdk = build()

        assert await client.object_exists(KEY) is True
        assert sdk.operations == ["head_object"]

    async def test_false_on_a_404_rather_than_an_exception(self) -> None:
        client, _, _ = build(FakeSdkClient(error=client_error("404", 404, "HeadObject")))

        assert await client.object_exists(KEY) is False

    @pytest.mark.parametrize(
        "error",
        [
            client_error("AccessDenied", 403),
            client_error("SlowDown", 503),
            client_error("InternalError", 500),
            boto_errors.EndpointConnectionError(endpoint_url="http://minio:9000"),
        ],
    )
    async def test_every_other_failure_still_raises(self, error: BaseException) -> None:
        """Swallowing an outage into `False` is the answer most likely to delete something."""
        client, _, _ = build(FakeSdkClient(error=error))

        with pytest.raises(ExternalServiceError):
            await client.object_exists(KEY)


class TestDeleteObject:
    async def test_it_sends_the_bucket_and_key(self) -> None:
        client, _, sdk = build()

        assert await client.delete_object(KEY) is None
        assert sdk.calls == [("delete_object", {"Bucket": BUCKET, "Key": KEY})]


class TestPresignedGetUrl:
    async def test_the_parameters_it_assembles(self) -> None:
        client, _, sdk = build()

        url = await client.presigned_get_url(KEY, expires_in=900)

        assert url == sdk.url
        assert sdk.calls == [
            (
                "generate_presigned_url",
                {
                    "operation": "get_object",
                    "Params": {"Bucket": BUCKET, "Key": KEY},
                    "ExpiresIn": 900,
                },
            )
        ]

    async def test_a_filename_becomes_a_quoted_content_disposition(self) -> None:
        """Unquoted, a filename with a space truncates the header value."""
        client, _, sdk = build()

        await client.presigned_get_url(KEY, expires_in=60, filename="my report.csv")

        assert sdk.calls[0][1]["Params"]["ResponseContentDisposition"] == (
            'attachment; filename="my report.csv"'
        )

    async def test_no_disposition_is_sent_when_no_filename_is_given(self) -> None:
        client, _, sdk = build()

        await client.presigned_get_url(KEY, expires_in=60)

        assert set(sdk.calls[0][1]["Params"]) == {"Bucket", "Key"}

    async def test_it_does_not_contact_s3_to_check_the_object_exists(self) -> None:
        """Presigning is local arithmetic; the service is what has to `HEAD` first."""
        client, _, sdk = build()

        await client.presigned_get_url(KEY, expires_in=60)

        assert sdk.operations == ["generate_presigned_url"]


# ---------------------------------------------------------------------------------------
# Secrets and logging
# ---------------------------------------------------------------------------------------


class TestNothingSecretIsWrittenDown:
    async def test_a_presigned_url_is_never_logged(self) -> None:
        """The URL *is* a credential: anyone holding it can fetch the object."""
        client, _, sdk = build()

        with capture_logs() as entries:
            url = await client.presigned_get_url(KEY, expires_in=900)

        rendered = repr(entries)
        assert url == sdk.url
        assert url not in rendered
        assert "X-Amz-Signature" not in rendered
        assert "deadbeefcafe" not in rendered

    async def test_the_completed_line_says_the_result_was_withheld(self) -> None:
        client, _, _ = build()

        with capture_logs() as entries:
            await client.presigned_get_url(KEY, expires_in=900)

        completed = [entry for entry in entries if entry["event"] == "s3.request.completed"]
        assert completed and completed[0]["result"] == "withheld"

    async def test_the_secret_appears_in_no_log_line_on_the_happy_path(self) -> None:
        client, _, _ = build()

        with capture_logs() as entries:
            await client.put_object(KEY, b"x", content_type="text/csv")

        assert SECRET not in repr(entries)

    async def test_the_secret_appears_in_no_log_line_on_a_failure(self) -> None:
        client, _, _ = build(FakeSdkClient(error=client_error("SignatureDoesNotMatch", 403)))

        with capture_logs() as entries, pytest.raises(ExternalServiceError):
            await client.get_object(KEY)

        assert SECRET not in repr(entries)

    async def test_a_library_message_quoting_the_secret_is_scrubbed(self) -> None:
        """The last line of defence, for text this module did not compose."""
        client, _, _ = build(FakeSdkClient(error=RuntimeError(f"boom {SECRET} boom")))

        with capture_logs() as entries, pytest.raises(ExternalServiceError):
            await client.get_object(KEY)

        failed = [entry for entry in entries if entry["event"] == "s3.request.failed"]
        assert failed
        assert SECRET not in failed[0]["error"]
        assert REDACTED in failed[0]["error"]

    async def test_the_secret_appears_in_no_exception_message_or_details(self) -> None:
        client, _, _ = build(FakeSdkClient(error=RuntimeError(f"boom {SECRET} boom")))

        with pytest.raises(ExternalServiceError) as caught:
            await client.get_object(KEY)

        assert SECRET not in str(caught.value)
        assert SECRET not in str(caught.value.details)

    async def test_the_key_and_bucket_are_logged_because_that_is_the_diagnosis(self) -> None:
        """They are kept out of `details` (public) and put in the log (operator)."""
        client, _, _ = build(FakeSdkClient(error=client_error("NoSuchKey", 404)))

        with capture_logs() as entries, pytest.raises(ExternalServiceError):
            await client.get_object(KEY)

        failed = next(entry for entry in entries if entry["event"] == "s3.request.failed")
        assert failed["key"] == KEY
        assert failed["bucket"] == BUCKET
        assert failed["reason"] == "object_not_found"
        assert failed["aws_error_code"] == "NoSuchKey"
        assert failed["status_code"] == 404


# ---------------------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------------------


class TestLifecycle:
    async def test_no_client_is_constructed_until_the_first_call(self) -> None:
        client, session, _ = build()

        assert session.entered == 0
        await client.head_object(KEY)
        assert session.entered == 1

    async def test_the_client_is_reused_across_calls(self) -> None:
        """The pool is why holding a client for more than one call is worth anything."""
        client, session, _ = build()

        await client.head_object(KEY)
        await client.head_object(KEY)

        assert session.entered == 1

    async def test_aclose_unwinds_the_context(self) -> None:
        client, session, _ = build()
        await client.head_object(KEY)

        await client.aclose()

        assert session.exited == 1
        assert client.is_closed is True

    async def test_aclose_is_idempotent(self) -> None:
        client, session, _ = build()
        await client.head_object(KEY)

        await client.aclose()
        await client.aclose()

        assert session.exited == 1

    async def test_closing_one_that_never_connected_is_fine(self) -> None:
        client, session, _ = build()

        await client.aclose()

        assert session.exited == 0

    async def test_a_call_after_closing_raises_rather_than_reopening(self) -> None:
        """Silently reconnecting hides a lifecycle bug in something meant to be long-lived."""
        client, _, _ = build()
        await client.aclose()

        with pytest.raises(RuntimeError, match="closed"):
            await client.head_object(KEY)

    async def test_the_context_manager_closes_it(self) -> None:
        session = FakeSession()

        async with S3Client(settings(), session=session) as client:
            await client.head_object(KEY)

        assert session.exited == 1
        assert client.is_closed is True


class TestDefaultSession:
    def test_it_is_an_aioboto3_session_built_once(self) -> None:
        """A `Session` holds botocore's service-model cache, no socket and no event loop."""
        assert isinstance(default_session(), aioboto3.Session)
        assert default_session() is default_session()

    def test_a_client_uses_it_unless_one_is_injected(self) -> None:
        assert S3Client(settings())._session is default_session()
        session = FakeSession()
        assert S3Client(settings(), session=session)._session is session


class TestTheReturnedModels:
    def test_they_are_frozen_because_a_vendor_answer_is_not_editable(self) -> None:
        info = S3ObjectInfo(key="k")

        with pytest.raises(ValidationError):
            info.key = "other"  # type: ignore[misc]

    def test_size_is_what_arrived_not_what_s3_claimed(self) -> None:
        assert S3Object(key="k", body=b"1234").size_bytes == 4

    def test_an_etag_is_reported_with_its_quotes(self) -> None:
        """Unquoting would be this layer reshaping a vendor value."""
        assert S3ObjectInfo(key="k", etag='"abc"').etag == '"abc"'


class TestItIsNotOnTheHttpBase:
    def test_the_client_does_not_subclass_base_http_client(self) -> None:
        """An SDK offers no seam to hand it an `httpx.AsyncClient`; see the module docstring."""
        from app.clients.base import BaseHTTPClient

        assert not issubclass(S3Client, BaseHTTPClient)

    def test_but_it_keeps_the_shared_half_of_the_contract(self) -> None:
        client, _, _ = build()

        assert hasattr(client, "vendor")
        assert callable(client.aclose)
        assert isinstance(client._error(S3Failure.TRANSPORT), ExternalServiceError)

    def test_a_uuid_shaped_key_is_just_a_string_to_this_layer(self) -> None:
        """A client knows nothing about Anvex: no owner, no export, no domain import."""
        source = Path(s3_module.__file__).read_text(encoding="utf-8")

        assert uuid.UUID(KEY.split("/")[2])
        assert "app.domain.storage" not in source
        assert "from app.domain import" not in source
