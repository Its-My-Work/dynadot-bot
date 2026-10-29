"""Application settings loaded from environment / .env file."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
    # NoDecode: without it pydantic-settings JSON-decodes the env var *before*
    # the validator runs, so `SUPPORTED_LANGUAGES=ru,en` would raise.
    supported_languages: Annotated[list[str], NoDecode] = ["ru", "en"]

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
    platega_timeout_seconds: float = 20.0
    # 2 = SBP QR, the default RU retail method.
    platega_payment_method: int = 2
    payment_return_url: str | None = None
    payment_failed_url: str | None = None

    # --- Telegram bot runtime ---------------------------------------------
    # Long polling locally; set a public base URL to switch to webhooks.
    telegram_webhook_base_url: str | None = None
    telegram_webhook_path: str = "/api/v1/webhooks/telegram"
    # Telegram ids allowed into admin-only bot commands.
    telegram_admin_ids: Annotated[list[int], NoDecode] = []
    support_username: str | None = None

    @field_validator("telegram_admin_ids", mode="before")
    @classmethod
    def _split_admin_ids(cls, value: object) -> object:
        """Accept `TELEGRAM_ADMIN_IDS=1,2` as well as a JSON list.

        ``NoDecode`` turns off pydantic-settings' JSON pre-parsing (so the
        comma form reaches us at all), which means JSON must be handled here.
        """
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            import json

            return json.loads(text)
        return [int(part) for part in text.split(",") if part.strip()]

    @field_validator("supported_languages", mode="before")
    @classmethod
    def _split_languages(cls, value: object) -> object:
        """Accept `SUPPORTED_LANGUAGES=ru,en` as well as a JSON list."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            import json

            return json.loads(text)
        return [item.strip() for item in text.split(",") if item.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "prod"

    @property
    def dynadot_configured(self) -> bool:
        return bool(self.dynadot_api_key and self.dynadot_api_secret)

    @property
    def platega_configured(self) -> bool:
        return bool(self.platega_merchant_id and self.platega_secret)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token)

    @property
    def telegram_webhook_url(self) -> str | None:
        """Full webhook URL, or None when long polling should be used."""
        if not self.telegram_webhook_base_url:
            return None
        return (
            f"{self.telegram_webhook_base_url.rstrip('/')}"
            f"/{self.telegram_webhook_path.lstrip('/')}"
        )


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor (safe to use as a FastAPI dependency)."""
    return Settings()


settings = get_settings()
