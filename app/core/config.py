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
    admin_username: str | None = None
    admin_password: SecretStr | None = None

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

    # --- Registration worker ----------------------------------------------
    # Run the worker inside the API process. Keep it False when
    # `python -m app.worker` runs as its own service: two workers would fight
    # over tasks and waste Dynadot's single request slot.
    registration_worker_enabled: bool = False
    registration_worker_interval: float = 5.0
    registration_max_attempts: int = 4

    # --- Payments (Platega / RU gateways) ---------------------------------
    platega_merchant_id: str | None = None
    platega_secret: SecretStr | None = None
    platega_base_url: str = "https://app.platega.io"
    platega_timeout_seconds: float = 20.0
    # 2 = SBP QR, the default RU retail method.
    platega_payment_method: int = 2
    payment_return_url: str | None = None
    payment_failed_url: str | None = None

    # --- VPS / VPN provisioning -------------------------------------------
    # Which upstream cloud new servers are rented from. "mock" (or any
    # provider whose token is unset) keeps the catalogue and checkout working
    # without credentials instead of 500-ing.
    vps_provider: str = "mock"
    timeweb_api_token: SecretStr | None = None
    aeza_api_token: SecretStr | None = None
    pq_hosting_api_token: SecretStr | None = None
    vps_timeout_seconds: float = 30.0
    # Masquerade host for the Reality handshake.
    vps_reality_sni: str = "www.microsoft.com"
    # Paid term length for one server.
    vps_term_days: int = 30

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

    def vps_api_token_for(self, provider: object) -> str | None:
        """Return the API token for ``provider``, or None when unconfigured.

        Takes the enum (or its value) rather than reading ``vps_provider`` so
        callers can ask about a specific upstream — the admin panel shows which
        of the three are usable.
        """
        name = getattr(provider, "value", provider)
        secret = {
            "timeweb": self.timeweb_api_token,
            "aeza": self.aeza_api_token,
            "pq_hosting": self.pq_hosting_api_token,
        }.get(str(name))
        return secret.get_secret_value() if secret else None

    @property
    def vps_configured(self) -> bool:
        """True when the selected provider can actually provision."""
        return self.vps_provider != "mock" and bool(
            self.vps_api_token_for(self.vps_provider)
        )

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


def get_settings() -> Settings:
    """Return the application settings singleton."""
    return settings


# Backwards compatibility for test fixtures calling get_settings.cache_clear()
get_settings.cache_clear = lambda: None  # type: ignore[attr-defined]

settings = Settings()
