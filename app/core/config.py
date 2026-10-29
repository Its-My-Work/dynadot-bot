"""Application settings loaded from environment / .env file."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "dev", "staging", "prod"]


class Settings(BaseSettings):
    """Central configuration object.

    Every value is overridable via environment variables so the same image can
    run locally (SQLite) and in production (PostgreSQL) without code changes.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    # --- General -----------------------------------------------------------
    app_name: str = "Dynadot Domain Service"
    environment: Environment = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    secret_key: SecretStr = SecretStr("change-me-in-production")

    # --- Database ----------------------------------------------------------
    # Async driver DSN. PostgreSQL in production, SQLite for tests/local runs.
    database_url: str = "sqlite+aiosqlite:///./dynadot_bot.db"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # --- Internationalisation ---------------------------------------------
    default_language: str = "ru"
    supported_languages: list[str] = ["ru", "en"]

    # --- Telegram ----------------------------------------------------------
    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None
    tma_url: str | None = None

    # --- Dynadot -----------------------------------------------------------
    dynadot_base_url: str = "https://api-sandbox.dynadot.com"
    dynadot_api_key: SecretStr | None = None
    dynadot_api_secret: SecretStr | None = None
    # Dynadot processes ONE request at a time on the Regular price level.
    dynadot_max_concurrency: int = 1
    dynadot_rate_limit_per_minute: int = 60
    dynadot_timeout_seconds: float = 30.0
    dynadot_max_retries: int = 4

    # --- Pricing -----------------------------------------------------------
    default_markup_percent: float = 25.0
    usd_to_rub_rate: float = 95.0

    # --- Payments (Platega / RU gateways) ---------------------------------
    platega_merchant_id: str | None = None
    platega_secret: SecretStr | None = None
    platega_base_url: str = "https://app.platega.io"
    payment_return_url: str | None = None

    @field_validator("supported_languages", mode="before")
    @classmethod
    def _split_languages(cls, value: object) -> object:
        """Allow `SUPPORTED_LANGUAGES=ru,en` in addition to JSON lists."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "prod"

    @property
    def dynadot_configured(self) -> bool:
        return bool(self.dynadot_api_key and self.dynadot_api_secret)


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor (safe to use as a FastAPI dependency)."""
    return Settings()


settings = get_settings()
