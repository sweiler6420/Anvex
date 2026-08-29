"""Unit tests for `app.settings`.

These must never depend on the developer's real repo-root `.env`, so every test either
points `Settings` at the committed `.env.example` or supplies an explicitly empty env
file, and always clears `get_settings`' cache.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from app.settings import ENV_FILE, REPO_ROOT, Settings, get_settings

EXAMPLE_ENV = REPO_ROOT / ".env.example"

# Every var the backend reads. `.env.example` is the committed source of truth; if a key
# is added there without a matching Settings field this list keeps the drift visible.
BACKEND_ENV_VARS = [
    "ANVEX_ENV",
    "LOG_LEVEL",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_SCHEMA",
    "REDIS_HOST",
    "REDIS_PORT",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "S3_ENDPOINT_URL",
    "S3_REGION",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "S3_BUCKET",
    "JWT_SECRET_KEY",
    "JWT_ALGORITHM",
    "JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
    "JWT_REFRESH_TOKEN_EXPIRE_MINUTES",
    "ALPHAVANTAGE_API_KEY",
    "NEWSAPI_API_KEY",
    "API_HOST",
    "API_PORT",
    "API_CORS_ORIGINS",
]


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip any real backend config from the process env and reset the settings cache."""
    for name in BACKEND_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def example_settings() -> Settings:
    """Build a `Settings` from the committed `.env.example`, not the developer's `.env`."""
    return Settings(_env_file=EXAMPLE_ENV)


# --------------------------------------------------------------------------- wiring


def test_env_file_points_at_the_repo_root() -> None:
    assert ENV_FILE == REPO_ROOT / ".env"
    assert (REPO_ROOT / "CLAUDE.md").is_file(), "REPO_ROOT should resolve to the monorepo root"


def test_env_example_exists_and_is_readable() -> None:
    assert EXAMPLE_ENV.is_file()


# ------------------------------------------------------------------------- defaults


def test_values_load_from_env_example() -> None:
    settings = example_settings()

    assert settings.anvex_env == "local"
    assert settings.log_level == "INFO"

    assert settings.postgres_user == "anvex"
    assert settings.postgres_password.get_secret_value() == "anvex"
    assert settings.postgres_db == "anvex"
    assert settings.postgres_host == "db"
    assert settings.postgres_port == 5432
    assert settings.postgres_schema == "anvex"

    assert settings.redis_host == "redis"
    assert settings.redis_port == 6379
    assert settings.celery_broker_url == "redis://redis:6379/0"
    assert settings.celery_result_backend == "redis://redis:6379/1"

    assert settings.s3_endpoint_url == "http://minio:9000"
    assert settings.s3_region == "us-east-1"
    assert settings.s3_access_key_id == "anvexminio"
    assert settings.s3_secret_access_key.get_secret_value() == "anvexminio"
    assert settings.s3_bucket == "anvex-local"

    assert settings.jwt_secret_key.get_secret_value() == "change-me-in-production"
    assert settings.jwt_algorithm == "HS256"
    assert settings.jwt_access_token_expire_minutes == 30
    assert settings.jwt_refresh_token_expire_minutes == 10080

    assert settings.alphavantage_api_key.get_secret_value() == ""
    assert settings.newsapi_api_key.get_secret_value() == ""

    assert settings.api_host == "0.0.0.0"
    assert settings.api_port == 8000


def test_field_defaults_match_env_example_without_any_file() -> None:
    """Class defaults mirror `.env.example`, so a missing `.env` is not a broken app."""
    from_file = example_settings()
    from_defaults = Settings(_env_file=None)

    assert from_defaults.model_dump() == from_file.model_dump()


def test_settings_ignores_unknown_keys() -> None:
    """`.env.example` carries frontend-only `VITE_*` vars; they must not fail validation."""
    assert "VITE_API_BASE_URL" in EXAMPLE_ENV.read_text(encoding="utf-8")
    settings = example_settings()
    assert not hasattr(settings, "vite_api_base_url")


def test_port_fields_are_coerced_to_int(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("API_PORT", "9001")

    settings = example_settings()

    assert settings.postgres_port == 6543
    assert settings.api_port == 9001


# -------------------------------------------------------------------- env precedence


def test_real_environment_overrides_the_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "prod-db.internal")
    monkeypatch.setenv("ANVEX_ENV", "prod")
    monkeypatch.setenv("JWT_SECRET_KEY", "from-the-environment")

    settings = example_settings()

    assert settings.postgres_host == "prod-db.internal"
    assert settings.anvex_env == "prod"
    assert settings.jwt_secret_key.get_secret_value() == "from-the-environment"
    # untouched keys still come from the file
    assert settings.postgres_db == "anvex"


def test_env_var_lookup_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("log_level", "DEBUG")

    assert example_settings().log_level == "DEBUG"


# ------------------------------------------------------------------------------ DSNs


def test_postgres_dsn_is_an_asyncpg_sqlalchemy_url() -> None:
    settings = example_settings()

    assert settings.postgres_dsn == "postgresql+asyncpg://anvex:anvex@db:5432/anvex"


def test_postgres_sync_dsn_is_a_blocking_url() -> None:
    settings = example_settings()

    assert settings.postgres_sync_dsn == "postgresql+psycopg://anvex:anvex@db:5432/anvex"


def test_dsns_are_built_from_the_individual_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_USER", "someone")
    monkeypatch.setenv("POSTGRES_PASSWORD", "hunter2")
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "55432")
    monkeypatch.setenv("POSTGRES_DB", "anvex_test")

    settings = example_settings()

    assert (
        settings.postgres_dsn == "postgresql+asyncpg://someone:hunter2@localhost:55432/anvex_test"
    )
    assert settings.postgres_sync_dsn == (
        "postgresql+psycopg://someone:hunter2@localhost:55432/anvex_test"
    )


def test_dsn_url_encodes_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_USER", "an@user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss:w/rd")

    dsn = example_settings().postgres_dsn

    assert dsn.startswith("postgresql+asyncpg://an%40user:p%40ss%3Aw%2Frd@")


def test_redis_url_is_built_from_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_HOST", "cache")
    monkeypatch.setenv("REDIS_PORT", "6380")

    assert example_settings().redis_url == "redis://cache:6380"


# ------------------------------------------------------------------------------ CORS


def test_cors_origins_parses_the_comma_separated_default() -> None:
    assert example_settings().cors_origins == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def test_cors_origins_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_CORS_ORIGINS", " http://a.test , http://b.test ,http://c.test ")

    assert example_settings().cors_origins == [
        "http://a.test",
        "http://b.test",
        "http://c.test",
    ]


def test_cors_origins_handles_a_single_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_CORS_ORIGINS", "http://only.test")

    assert example_settings().cors_origins == ["http://only.test"]


def test_cors_origins_drops_empty_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_CORS_ORIGINS", "http://a.test,,  ,http://b.test,")

    assert example_settings().cors_origins == ["http://a.test", "http://b.test"]


def test_cors_origins_is_empty_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_CORS_ORIGINS", "")

    assert example_settings().cors_origins == []


# --------------------------------------------------------------------------- secrets

SECRET_FIELDS = [
    "postgres_password",
    "jwt_secret_key",
    "s3_secret_access_key",
    "alphavantage_api_key",
    "newsapi_api_key",
]


@pytest.mark.parametrize("field", SECRET_FIELDS)
def test_secret_fields_are_secret_str(field: str) -> None:
    assert isinstance(getattr(example_settings(), field), SecretStr)


def test_secret_values_are_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "super-secret-pg")
    monkeypatch.setenv("JWT_SECRET_KEY", "super-secret-jwt")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "super-secret-s3")
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "super-secret-av")
    monkeypatch.setenv("NEWSAPI_API_KEY", "super-secret-news")

    settings = example_settings()
    rendered = f"{settings!r}{settings!s}{settings.model_dump()}"

    for leaked in (
        "super-secret-pg",
        "super-secret-jwt",
        "super-secret-s3",
        "super-secret-av",
        "super-secret-news",
    ):
        assert leaked not in rendered

    assert "**********" in repr(settings)
    # non-secret fields are still visible, so the repr stays useful for debugging
    assert "postgres_host" in repr(settings)


def test_dsns_are_not_exposed_in_repr_or_dump() -> None:
    """The DSNs embed the password, so they stay out of serialised output."""
    settings = example_settings()

    assert "postgres_dsn" not in repr(settings)
    assert "postgres_dsn" not in settings.model_dump()


# -------------------------------------------------------------------------- accessor


def test_get_settings_returns_a_settings_instance() -> None:
    assert isinstance(get_settings(), Settings)


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_get_settings_cache_clear_rebuilds(monkeypatch: pytest.MonkeyPatch) -> None:
    first = get_settings()
    get_settings.cache_clear()
    monkeypatch.setenv("ANVEX_ENV", "staging")
    second = get_settings()

    assert second is not first
    assert second.anvex_env == "staging"
    assert get_settings() is second
