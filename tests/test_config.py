"""Settings parsing tests.

List-valued settings are the fragile part: pydantic-settings JSON-decodes
complex types from the environment *before* validators run, which made
`SUPPORTED_LANGUAGES=ru,en` (the form documented in `.env.example`) raise.
`NoDecode` disables that pre-parsing, so the validators must accept both the
comma form and JSON.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Settings reads a .env file; clear the vars under test for determinism."""
    for key in (
        "SUPPORTED_LANGUAGES",
        "TELEGRAM_ADMIN_IDS",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_WEBHOOK_BASE_URL",
        "PLATEGA_MERCHANT_ID",
        "PLATEGA_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)


# ------------------------------------------------------------ list parsing


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ru,en", ["ru", "en"]),
        ("ru, en", ["ru", "en"]),
        ('["ru","en"]', ["ru", "en"]),
        ("ru", ["ru"]),
        ("", []),
    ],
)
def test_supported_languages_accepts_comma_and_json(monkeypatch, raw, expected):
    monkeypatch.setenv("SUPPORTED_LANGUAGES", raw)
    assert Settings().supported_languages == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("111,222", [111, 222]),
        ("111, 222", [111, 222]),
        ("[5,6]", [5, 6]),
        ("", []),
    ],
)
def test_admin_ids_accepts_comma_and_json(monkeypatch, raw, expected):
    monkeypatch.setenv("TELEGRAM_ADMIN_IDS", raw)
    assert Settings().telegram_admin_ids == expected


def test_list_defaults_apply_when_unset():
    settings = Settings()
    assert settings.supported_languages == ["ru", "en"]
    assert settings.telegram_admin_ids == []


# ---------------------------------------------------------- derived flags


def test_platega_configured_requires_both_credentials():
    assert Settings(platega_merchant_id="m", platega_secret="s").platega_configured
    assert not Settings(platega_merchant_id="m", platega_secret=None).platega_configured
    assert not Settings(platega_merchant_id=None, platega_secret="s").platega_configured


def test_telegram_configured_tracks_the_token():
    assert Settings(telegram_bot_token="123:abc").telegram_configured
    assert not Settings(telegram_bot_token=None).telegram_configured


def test_webhook_url_is_none_without_a_base_url():
    """None means "use long polling" — the API must not call setWebhook."""
    assert Settings(telegram_webhook_base_url=None).telegram_webhook_url is None


def test_webhook_url_joins_base_and_path_without_double_slashes():
    settings = Settings(
        telegram_webhook_base_url="https://api.example.test/",
        telegram_webhook_path="/api/v1/webhooks/telegram",
    )
    assert (
        settings.telegram_webhook_url
        == "https://api.example.test/api/v1/webhooks/telegram"
    )


def test_webhook_url_handles_a_path_without_a_leading_slash():
    settings = Settings(
        telegram_webhook_base_url="https://api.example.test",
        telegram_webhook_path="hook",
    )
    assert settings.telegram_webhook_url == "https://api.example.test/hook"


def test_platega_defaults_target_the_documented_host_and_sbp():
    settings = Settings()
    assert settings.platega_base_url == "https://app.platega.io"
    # 2 = SBP QR, the default RU retail method.
    assert settings.platega_payment_method == 2
