"""Single configuration entry point for the Anvex backend.

Per ``CLAUDE.md`` §4 this is the **only** module in the backend allowed to read the
environment. Everything else receives a :class:`Settings` instance (via ``app/deps``)
rather than calling ``os.getenv``.

Configuration comes from the one repo-root ``.env`` (``CLAUDE.md`` §2). The path is
resolved from ``__file__`` so it holds regardless of the process working directory, and
real environment variables still win over the file — that is pydantic-settings' default
precedence and we do not override it.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

#: ``backend/`` — the directory containing the ``app`` package.
BACKEND_DIR: Path = Path(__file__).resolve().parent.parent
#: The monorepo root, which holds the single ``.env`` shared by every stack.
REPO_ROOT: Path = BACKEND_DIR.parent
#: The one and only env file. Missing is fine: real env vars / defaults take over.
ENV_FILE: Path = REPO_ROOT / ".env"


class Settings(BaseSettings):
    """Typed view of the repo-root ``.env``.

    Field names are the lower-cased env var names; lookup is case-insensitive, so
    ``POSTGRES_HOST`` populates :attr:`postgres_host`. Unknown keys (``VITE_*``, which are
    frontend-only) are ignored rather than raising.
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ----- General -----
    anvex_env: str = "local"
    log_level: str = "INFO"

    # ----- Postgres -----
    postgres_user: str = "anvex"
    postgres_password: SecretStr = SecretStr("anvex")
    postgres_db: str = "anvex"
    postgres_host: str = "db"
    postgres_port: int = 5432
    postgres_schema: str = "anvex"

    # ----- Redis (Celery broker + result backend) -----
    redis_host: str = "redis"
    redis_port: int = 6379
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"

    # ----- S3 (MinIO locally, real S3 in AWS) -----
    s3_endpoint_url: str | None = "http://minio:9000"
    s3_region: str = "us-east-1"
    s3_access_key_id: str = "anvexminio"
    s3_secret_access_key: SecretStr = SecretStr("anvexminio")
    s3_bucket: str = "anvex-local"

    # ----- Auth / JWT -----
    jwt_secret_key: SecretStr = SecretStr("change-me-in-production")
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_minutes: int = 10080

    # ----- Third-party clients -----
    alphavantage_api_key: SecretStr = SecretStr("")
    newsapi_api_key: SecretStr = SecretStr("")

    # ----- API -----
    # Binding all interfaces is intended: the API always runs behind a container boundary.
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        description="Comma-separated origin list; parsed by `cors_origins`.",
    )

    # ------------------------------------------------------------------
    # Computed views
    #
    # Deliberately plain properties rather than `computed_field`: the DSNs embed the
    # Postgres password, and a computed_field would put it back into `model_dump()`
    # and `repr()`, defeating the SecretStr fields above.
    # ------------------------------------------------------------------

    @property
    def postgres_dsn(self) -> str:
        """Async SQLAlchemy URL used by the app engine (ANV-3 onward)."""
        return self._postgres_url("postgresql+asyncpg")

    @property
    def postgres_sync_dsn(self) -> str:
        """Blocking psycopg-style URL, for Alembic tooling that cannot go async."""
        return self._postgres_url("postgresql+psycopg")

    def _postgres_url(self, driver: str) -> str:
        user = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password.get_secret_value())
        host = f"{self.postgres_host}:{self.postgres_port}"
        return f"{driver}://{user}:{password}@{host}/{self.postgres_db}"

    @property
    def cors_origins(self) -> list[str]:
        """`API_CORS_ORIGINS` parsed into a list, ignoring blanks and stray whitespace."""
        return [origin.strip() for origin in self.api_cors_origins.split(",") if origin.strip()]

    @property
    def redis_url(self) -> str:
        """Base Redis URL (no database index) built from the host/port pair."""
        return f"redis://{self.redis_host}:{self.redis_port}"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings`, constructed once.

    Call ``get_settings.cache_clear()`` in tests after patching the environment.
    """
    return Settings()
