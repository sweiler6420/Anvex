"""Unit tests for ``app/domain/auth.py``.

Pure tier: no fixtures, no app, no database, and — the point of the module — **no
``sleep``**. Every expiry test simply hands the decoder a different ``now``.

The clock values below are deliberately nowhere near the real one, so a test that passes
could not have passed by accidentally reading the wall clock.
"""

from __future__ import annotations

import ast
import base64
import inspect
import json
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from jose import jwt

from app.domain import auth
from app.domain.auth import (
    ACCESS_TOKEN_TYPE,
    REFRESH_TOKEN_TYPE,
    ExpiredTokenError,
    InvalidTokenError,
    TokenError,
    WrongTokenTypeError,
    build_claims,
    create_token,
    create_token_pair,
    decode_access_token,
    decode_refresh_token,
    decode_token,
    encode_token,
)
from app.domain.errors import AnvexError, UnauthorizedError
from app.middleware.errors import status_for
from app.schemas.auth import TokenPair, TokenPayload

SECRET = "unit-test-secret-value"
OTHER_SECRET = "a-different-unit-test-secret"
ALGORITHM = "HS256"
SUBJECT = uuid.UUID("11111111-2222-3333-4444-555555555555")

#: Fixed, and a long way from today in both directions.
NOW = datetime(2024, 3, 1, 12, 0, 0, tzinfo=UTC)
LONG_AGO = datetime(1999, 12, 31, 23, 59, 0, tzinfo=UTC)
FAR_FUTURE = datetime(2099, 6, 1, 8, 30, 0, tzinfo=UTC)

ACCESS_LIFETIME = timedelta(minutes=30)
REFRESH_LIFETIME = timedelta(days=7)


def mint(
    *,
    token_type: str = ACCESS_TOKEN_TYPE,
    now: datetime = NOW,
    lifetime: timedelta = ACCESS_LIFETIME,
    secret: str = SECRET,
    algorithm: str = ALGORITHM,
    subject: uuid.UUID = SUBJECT,
) -> str:
    return create_token(
        subject=subject,
        token_type=token_type,  # type: ignore[arg-type]
        now=now,
        lifetime=lifetime,
        secret=secret,
        algorithm=algorithm,
    )


def read(
    token: str | None,
    *,
    expected_type: str = ACCESS_TOKEN_TYPE,
    now: datetime = NOW,
    secret: str = SECRET,
    algorithm: str = ALGORITHM,
) -> TokenPayload:
    return decode_token(
        token,
        expected_type=expected_type,  # type: ignore[arg-type]
        now=now,
        secret=secret,
        algorithm=algorithm,
    )


def source_tree() -> ast.Module:
    return ast.parse(Path(auth.__file__).read_text(encoding="utf-8"))


class TestPurity:
    """``app/domain/`` is pure by rule, and prose conventions get broken (§3)."""

    def test_module_imports_no_framework_and_no_settings(self) -> None:
        roots: set[str] = set()
        modules: set[str] = set()
        for node in ast.walk(source_tree()):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)
        roots = {module.split(".")[0] for module in modules}

        assert "fastapi" not in roots
        assert "starlette" not in roots
        assert "sqlalchemy" not in roots
        assert "app.settings" not in modules
        # Downward-only dependencies: domain may lean on schemas and on its own errors,
        # never on a layer that performs I/O.
        assert {module for module in modules if module.startswith("app")} == {
            "app.domain.errors",
            "app.schemas.auth",
        }

    def test_module_never_reads_a_clock(self) -> None:
        """The whole reason expiry is testable without sleeping."""
        clock_calls = {"now", "utcnow", "today", "time", "monotonic", "perf_counter", "time_ns"}
        offenders = []
        for node in ast.walk(source_tree()):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in clock_calls:
                offenders.append(name)

        assert offenders == [], f"domain/auth.py must take the clock as a parameter: {offenders}"

    def test_module_source_mentions_no_clock_read(self) -> None:
        source = Path(auth.__file__).read_text(encoding="utf-8")
        assert "utcnow" not in source
        assert ".now(" not in source
        assert "get_settings" not in source


class TestBuildClaims:
    def test_shape(self) -> None:
        claims = build_claims(
            subject=SUBJECT, token_type=ACCESS_TOKEN_TYPE, now=NOW, lifetime=ACCESS_LIFETIME
        )
        assert claims == {
            "sub": str(SUBJECT),
            "type": "access",
            "iat": int(NOW.timestamp()),
            "exp": int((NOW + ACCESS_LIFETIME).timestamp()),
        }

    def test_iat_and_exp_come_from_the_injected_clock(self) -> None:
        claims = build_claims(
            subject=SUBJECT, token_type=ACCESS_TOKEN_TYPE, now=FAR_FUTURE, lifetime=ACCESS_LIFETIME
        )
        assert datetime.fromtimestamp(claims["iat"], tz=UTC) == FAR_FUTURE
        assert claims["exp"] - claims["iat"] == int(ACCESS_LIFETIME.total_seconds())

    def test_a_non_utc_offset_is_normalised_to_the_same_instant(self) -> None:
        elsewhere = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
        assert elsewhere.hour != NOW.hour, "the wall-clock reading really is different"
        assert build_claims(
            subject=SUBJECT, token_type=ACCESS_TOKEN_TYPE, now=elsewhere, lifetime=ACCESS_LIFETIME
        ) == build_claims(
            subject=SUBJECT, token_type=ACCESS_TOKEN_TYPE, now=NOW, lifetime=ACCESS_LIFETIME
        )

    def test_a_naive_now_is_refused(self) -> None:
        """Otherwise ``.timestamp()`` silently uses the server's local zone."""
        with pytest.raises(ValueError, match="timezone-aware"):
            build_claims(
                subject=SUBJECT,
                token_type=ACCESS_TOKEN_TYPE,
                now=NOW.replace(tzinfo=None),
                lifetime=ACCESS_LIFETIME,
            )

    @pytest.mark.parametrize("lifetime", [timedelta(0), timedelta(seconds=-1), -ACCESS_LIFETIME])
    def test_a_non_positive_lifetime_is_refused(self, lifetime: timedelta) -> None:
        with pytest.raises(ValueError, match="positive"):
            build_claims(subject=SUBJECT, token_type=ACCESS_TOKEN_TYPE, now=NOW, lifetime=lifetime)

    def test_claims_are_deterministic(self) -> None:
        first = build_claims(
            subject=SUBJECT, token_type=REFRESH_TOKEN_TYPE, now=NOW, lifetime=REFRESH_LIFETIME
        )
        second = build_claims(
            subject=SUBJECT, token_type=REFRESH_TOKEN_TYPE, now=NOW, lifetime=REFRESH_LIFETIME
        )
        assert first == second


class TestRoundTrip:
    def test_round_trip_preserves_the_subject(self) -> None:
        payload = read(mint())
        assert payload.sub == SUBJECT
        assert isinstance(payload.sub, uuid.UUID)

    def test_round_trip_preserves_the_type(self) -> None:
        assert read(mint(), expected_type=ACCESS_TOKEN_TYPE).type == "access"
        assert (
            read(
                mint(token_type=REFRESH_TOKEN_TYPE, lifetime=REFRESH_LIFETIME),
                expected_type=REFRESH_TOKEN_TYPE,
            ).type
            == "refresh"
        )

    def test_exp_and_iat_are_the_injected_clock_not_the_wall_clock(self) -> None:
        """Minted and read at a time nearly a century away — a wall-clock read anywhere in
        the module would make both assertions fail."""
        token = mint(now=FAR_FUTURE)
        payload = read(token, now=FAR_FUTURE + timedelta(minutes=1))
        assert payload.iat == FAR_FUTURE
        assert payload.exp == FAR_FUTURE + ACCESS_LIFETIME

    def test_a_token_minted_in_the_past_still_decodes_at_its_own_time(self) -> None:
        token = mint(now=LONG_AGO)
        assert read(token, now=LONG_AGO + timedelta(minutes=5)).iat == LONG_AGO

    def test_a_different_subject_round_trips(self) -> None:
        other = uuid.uuid4()
        assert read(mint(subject=other)).sub == other

    def test_encode_token_signs_what_it_is_given(self) -> None:
        claims: dict[str, Any] = {"sub": str(SUBJECT), "type": "access", "iat": 0, "exp": 1}
        token = encode_token(claims, secret=SECRET, algorithm=ALGORITHM)
        assert jwt.decode(token, SECRET, algorithms=[ALGORITHM], options={"verify_exp": False}) == (
            claims
        )


class TestExpiry:
    """Detected against the injected clock. Nothing here sleeps."""

    def test_valid_just_before_expiry(self) -> None:
        token = mint()
        payload = read(token, now=NOW + ACCESS_LIFETIME - timedelta(seconds=1))
        assert payload.sub == SUBJECT

    def test_expired_exactly_at_expiry(self) -> None:
        """``exp`` means "not valid at or after", so the boundary itself is a failure."""
        with pytest.raises(ExpiredTokenError):
            read(mint(), now=NOW + ACCESS_LIFETIME)

    def test_expired_after_expiry(self) -> None:
        with pytest.raises(ExpiredTokenError):
            read(mint(), now=NOW + ACCESS_LIFETIME + timedelta(seconds=1))

    def test_expired_by_years(self) -> None:
        with pytest.raises(ExpiredTokenError):
            read(mint(now=LONG_AGO), now=NOW)

    def test_expiry_is_checked_before_the_type(self) -> None:
        """An expired refresh token presented as an access token is *expired*: the type
        mismatch is not the interesting fact, and the client must not be told to refresh
        with something already dead."""
        token = mint(token_type=REFRESH_TOKEN_TYPE)
        with pytest.raises(ExpiredTokenError):
            read(token, expected_type=ACCESS_TOKEN_TYPE, now=NOW + ACCESS_LIFETIME)

    def test_a_naive_now_is_refused_by_the_decoder_too(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            read(mint(), now=NOW.replace(tzinfo=None))


class TestSignature:
    def test_a_token_signed_with_another_secret_is_rejected(self) -> None:
        with pytest.raises(InvalidTokenError):
            read(mint(secret=OTHER_SECRET))

    def test_a_token_signed_with_another_algorithm_is_rejected(self) -> None:
        with pytest.raises(InvalidTokenError):
            read(mint(algorithm="HS512"), algorithm=ALGORITHM)

    def test_a_tampered_signature_is_rejected(self) -> None:
        header, payload, signature = mint().split(".")
        flipped = signature[:-1] + ("A" if signature[-1] != "A" else "B")
        with pytest.raises(InvalidTokenError):
            read(f"{header}.{payload}.{flipped}")

    def test_a_tampered_payload_is_rejected(self) -> None:
        """The attack the signature exists to stop: re-sign the claims with a key we do
        not hold, and swap in someone else's subject."""
        forged_claims = build_claims(
            subject=uuid.uuid4(), token_type=ACCESS_TOKEN_TYPE, now=NOW, lifetime=ACCESS_LIFETIME
        )
        forged = encode_token(forged_claims, secret=OTHER_SECRET, algorithm=ALGORITHM)
        header, _, signature = mint().split(".")
        with pytest.raises(InvalidTokenError):
            read(f"{header}.{forged.split('.')[1]}.{signature}")

    def test_an_alg_none_token_is_rejected(self) -> None:
        """The classic JWT bypass: claim the token needs no signature."""

        def segment(value: dict[str, Any]) -> str:
            raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

        header = segment({"alg": "none", "typ": "JWT"})
        claims = segment(
            build_claims(
                subject=SUBJECT, token_type=ACCESS_TOKEN_TYPE, now=NOW, lifetime=ACCESS_LIFETIME
            )
        )
        with pytest.raises(InvalidTokenError):
            read(f"{header}.{claims}.")

    def test_a_token_with_its_signature_stripped_is_rejected(self) -> None:
        header, payload, _ = mint().split(".")
        with pytest.raises(InvalidTokenError):
            read(f"{header}.{payload}.")


class TestMalformedInput:
    @pytest.mark.parametrize(
        ("label", "token"),
        [
            ("none", None),
            ("empty", ""),
            ("whitespace", "   "),
            ("not-a-jwt", "hello"),
            ("two-segments", "aaaa.bbbb"),
            ("four-segments", "a.b.c.d"),
            ("empty-segments", ".."),
            ("not-base64", "!!!.???.###"),
            ("an-integer", 12345),
            ("a-dict", {"sub": str(SUBJECT)}),
            ("a-uuid", SUBJECT),
        ],
    )
    def test_garbage_is_rejected_as_an_invalid_token(self, label: str, token: Any) -> None:
        with pytest.raises(InvalidTokenError):
            read(token)

    def test_a_signed_token_missing_the_type_claim_is_invalid(self) -> None:
        """Not a *wrong* type — an unusable token. The check cannot be skipped by leaving
        the claim out, which is how a legacy token would look."""
        legacy = jwt.encode(
            {"sub": str(SUBJECT), "iat": int(NOW.timestamp()), "exp": int(NOW.timestamp()) + 60},
            SECRET,
            algorithm=ALGORITHM,
        )
        with pytest.raises(InvalidTokenError):
            read(legacy)

    def test_a_signed_token_with_an_unknown_type_is_invalid(self) -> None:
        forged = jwt.encode(
            {
                "sub": str(SUBJECT),
                "type": "admin",
                "iat": int(NOW.timestamp()),
                "exp": int(NOW.timestamp()) + 60,
            },
            SECRET,
            algorithm=ALGORITHM,
        )
        with pytest.raises(InvalidTokenError):
            read(forged)

    def test_a_signed_token_whose_subject_is_not_a_uuid_is_invalid(self) -> None:
        forged = jwt.encode(
            {
                "sub": "not-a-uuid",
                "type": "access",
                "iat": int(NOW.timestamp()),
                "exp": int(NOW.timestamp()) + 60,
            },
            SECRET,
            algorithm=ALGORITHM,
        )
        with pytest.raises(InvalidTokenError):
            read(forged)

    def test_a_signed_token_missing_exp_is_invalid(self) -> None:
        forged = jwt.encode(
            {"sub": str(SUBJECT), "type": "access", "iat": int(NOW.timestamp())},
            SECRET,
            algorithm=ALGORITHM,
        )
        with pytest.raises(InvalidTokenError):
            read(forged)


class TestTokenTypeIsEnforced:
    """The bug this ticket exists to close: the old ``/v1/refresh`` verified any token."""

    def test_a_refresh_token_is_rejected_where_an_access_token_is_required(self) -> None:
        token = mint(token_type=REFRESH_TOKEN_TYPE, lifetime=REFRESH_LIFETIME)
        with pytest.raises(WrongTokenTypeError) as caught:
            read(token, expected_type=ACCESS_TOKEN_TYPE)
        assert caught.value.details == {"expected_type": "access", "actual_type": "refresh"}

    def test_an_access_token_is_rejected_where_a_refresh_token_is_required(self) -> None:
        """The renewal hole itself: an access token traded for a fresh long-lived pair."""
        with pytest.raises(WrongTokenTypeError) as caught:
            read(mint(), expected_type=REFRESH_TOKEN_TYPE)
        assert caught.value.expected == "refresh"
        assert caught.value.actual == "access"

    def test_the_named_decoders_pin_their_type(self) -> None:
        access = mint()
        refresh = mint(token_type=REFRESH_TOKEN_TYPE, lifetime=REFRESH_LIFETIME)
        kwargs = {"now": NOW, "secret": SECRET, "algorithm": ALGORITHM}

        assert decode_access_token(access, **kwargs).sub == SUBJECT
        assert decode_refresh_token(refresh, **kwargs).sub == SUBJECT
        with pytest.raises(WrongTokenTypeError):
            decode_access_token(refresh, **kwargs)
        with pytest.raises(WrongTokenTypeError):
            decode_refresh_token(access, **kwargs)


class TestApiShapeForcesTheTypeCheck:
    """A rule enforced by a signature cannot be forgotten; one in a docstring can."""

    def test_expected_type_is_keyword_only_and_has_no_default(self) -> None:
        parameter = inspect.signature(decode_token).parameters["expected_type"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty

    def test_omitting_the_expected_type_is_an_error_at_the_call_site(self) -> None:
        with pytest.raises(TypeError, match="expected_type"):
            decode_token(mint(), now=NOW, secret=SECRET, algorithm=ALGORITHM)  # type: ignore[call-arg]

    def test_no_public_decoder_can_skip_stating_a_type(self) -> None:
        decoders = [
            getattr(auth, name)
            for name in auth.__all__
            if name.startswith("decode") and callable(getattr(auth, name))
        ]
        assert len(decoders) == 3
        for decoder in decoders:
            parameters = inspect.signature(decoder).parameters
            states_a_type = "expected_type" in parameters or decoder.__name__ in {
                "decode_access_token",
                "decode_refresh_token",
            }
            assert states_a_type, f"{decoder.__name__} decodes without naming a token type"

    def test_the_clock_and_the_key_material_are_injected_everywhere(self) -> None:
        for name in ("decode_token", "decode_access_token", "decode_refresh_token", "create_token"):
            parameters = inspect.signature(getattr(auth, name)).parameters
            for required in ("now", "secret", "algorithm"):
                parameter = parameters[required]
                assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, f"{name}.{required}"
                assert parameter.default is inspect.Parameter.empty, f"{name}.{required}"


class TestTokenPairCreation:
    def test_both_halves_are_minted_and_decode_as_their_own_type(self) -> None:
        pair = create_token_pair(
            subject=SUBJECT,
            now=NOW,
            access_lifetime=ACCESS_LIFETIME,
            refresh_lifetime=REFRESH_LIFETIME,
            secret=SECRET,
            algorithm=ALGORITHM,
        )
        assert isinstance(pair, TokenPair)
        assert pair.token_type == "bearer"
        assert pair.access_token != pair.refresh_token

        kwargs = {"now": NOW, "secret": SECRET, "algorithm": ALGORITHM}
        assert decode_access_token(pair.access_token, **kwargs).sub == SUBJECT
        assert decode_refresh_token(pair.refresh_token, **kwargs).sub == SUBJECT

    def test_the_lifetimes_differ_as_configured(self) -> None:
        pair = create_token_pair(
            subject=SUBJECT,
            now=NOW,
            access_lifetime=ACCESS_LIFETIME,
            refresh_lifetime=REFRESH_LIFETIME,
            secret=SECRET,
            algorithm=ALGORITHM,
        )
        kwargs = {"now": NOW, "secret": SECRET, "algorithm": ALGORITHM}
        access = decode_access_token(pair.access_token, **kwargs)
        refresh = decode_refresh_token(pair.refresh_token, **kwargs)

        assert access.iat == refresh.iat == NOW, "one clock reading for the pair"
        assert access.exp == NOW + ACCESS_LIFETIME
        assert refresh.exp == NOW + REFRESH_LIFETIME
        assert refresh.exp > access.exp

    def test_the_access_half_dies_first(self) -> None:
        pair = create_token_pair(
            subject=SUBJECT,
            now=NOW,
            access_lifetime=ACCESS_LIFETIME,
            refresh_lifetime=REFRESH_LIFETIME,
            secret=SECRET,
            algorithm=ALGORITHM,
        )
        later = {"now": NOW + ACCESS_LIFETIME, "secret": SECRET, "algorithm": ALGORITHM}
        with pytest.raises(ExpiredTokenError):
            decode_access_token(pair.access_token, **later)
        assert decode_refresh_token(pair.refresh_token, **later).sub == SUBJECT


class TestErrorClassification:
    """Three failures a caller acts on differently, all still a 401."""

    @pytest.mark.parametrize(
        "error_class", [TokenError, InvalidTokenError, ExpiredTokenError, WrongTokenTypeError]
    )
    def test_every_token_error_is_an_unauthorized_error(self, error_class: type) -> None:
        assert issubclass(error_class, TokenError)
        assert issubclass(error_class, UnauthorizedError)
        assert issubclass(error_class, AnvexError)
        assert status_for(error_class) == 401

    def test_the_codes_a_client_branches_on(self) -> None:
        assert InvalidTokenError.code == "invalid_token"
        assert ExpiredTokenError.code == "token_expired"
        assert WrongTokenTypeError.code == "wrong_token_type"

    def test_expired_is_distinguishable_from_invalid(self) -> None:
        """The frontend refreshes on one and logs out on the other."""
        with pytest.raises(TokenError) as expired:
            read(mint(), now=NOW + ACCESS_LIFETIME)
        with pytest.raises(TokenError) as invalid:
            read(mint(secret=OTHER_SECRET))
        assert expired.value.code != invalid.value.code

    def test_every_token_error_carries_a_message(self) -> None:
        assert InvalidTokenError().message
        assert ExpiredTokenError().message
        assert WrongTokenTypeError(expected="access", actual="refresh").message

    def test_no_token_error_leaks_the_token_or_the_secret(self) -> None:
        with pytest.raises(TokenError) as caught:
            read(mint(secret=OTHER_SECRET))
        rendered = f"{caught.value.message} {caught.value.details}"
        assert SECRET not in rendered
        assert OTHER_SECRET not in rendered
