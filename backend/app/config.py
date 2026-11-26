"""Application settings (environment variables, with defaults that work locally with no .env)."""

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.requests import HTTPConnection

# <repo>/backend/app/config.py -> <repo>/seed/seed.json
DEFAULT_SEED_PATH = Path(__file__).resolve().parents[2] / "seed" / "seed.json"

DEV_SECRET_KEY = "dev-insecure-secret"
MIN_PRODUCTION_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # The production guard rejects a model whose repr includes `secret_key`; keep it out of tracebacks.
        hide_input_in_errors=True,
    )

    database_url: str = "postgresql+asyncpg://l10:l10@localhost:15432/l10"
    test_database_url: str = "postgresql+asyncpg://l10:l10@localhost:15432/l10_test"
    app_now: datetime = datetime(2026, 11, 11, 15, 0, 0, tzinfo=UTC)
    app_clock: Literal["running", "frozen"] = "running"
    app_env: Literal["development", "test", "production"] = "development"
    secret_key: str = DEV_SECRET_KEY
    seed_path: Path = DEFAULT_SEED_PATH
    allowed_origins: str = "http://localhost:3000"
    cookie_secure: bool = False
    # Sign-in attempts per client IP per minute on POST /api/auth/login (unknown users count too); 0 disables.
    login_rate_limit_per_minute: int = Field(default=10, ge=0)

    @field_validator("app_now")
    @classmethod
    def _app_now_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _production_guard(self) -> Self:
        """Refuse to start in production with settings that are only acceptable on a developer machine."""
        if self.app_env != "production":
            return self
        problems: list[str] = []
        if self.secret_key == DEV_SECRET_KEY:
            problems.append("SECRET_KEY is the insecure development default; set a long random value")
        elif len(self.secret_key) < MIN_PRODUCTION_SECRET_LENGTH:
            problems.append(
                f"SECRET_KEY is too short ({len(self.secret_key)} chars); "
                f"use at least {MIN_PRODUCTION_SECRET_LENGTH}"
            )
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true (the session cookie must only travel over HTTPS)")
        if problems:
            raise ValueError("unsafe configuration for APP_ENV=production: " + "; ".join(problems))
        return self

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def settings_from(conn: HTTPConnection) -> Settings:
    """FastAPI dependency: the settings of the running app (works for HTTP and WebSocket)."""
    return conn.app.state.settings
