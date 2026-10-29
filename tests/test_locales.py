"""Locale catalogue integrity checks.

A key present in one language but not the other silently falls back to RU for
an EN user, which looks like a bug in production rather than a missing string.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

LOCALES_DIR = Path(__file__).parent.parent / "app" / "i18n" / "locales"
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def load(language: str) -> dict[str, str]:
    with (LOCALES_DIR / f"{language}.json").open(encoding="utf-8") as fh:
        return json.load(fh)


def test_ru_and_en_have_identical_key_sets():
    ru, en = load("ru"), load("en")
    assert set(ru) == set(en), {
        "only_ru": sorted(set(ru) - set(en)),
        "only_en": sorted(set(en) - set(ru)),
    }


def test_placeholders_match_across_languages():
    """A placeholder present in only one language breaks interpolation there."""
    ru, en = load("ru"), load("en")
    mismatched = {
        key: (sorted(PLACEHOLDER.findall(ru[key])), sorted(PLACEHOLDER.findall(en[key])))
        for key in ru
        if sorted(PLACEHOLDER.findall(ru[key])) != sorted(PLACEHOLDER.findall(en[key]))
    }
    assert mismatched == {}


@pytest.mark.parametrize("language", ["ru", "en"])
def test_no_empty_translations(language):
    catalog = load(language)
    assert [k for k, v in catalog.items() if not str(v).strip()] == []


@pytest.mark.parametrize("language", ["ru", "en"])
def test_every_order_status_has_a_localised_label(language):
    """The bot's order list renders `status.<value>` for each status."""
    from app.models.enums import OrderStatus

    catalog = load(language)
    missing = [s.value for s in OrderStatus if f"status.{s.value}" not in catalog]
    assert missing == []


@pytest.mark.parametrize("language", ["ru", "en"])
def test_payment_and_bot_keys_used_by_code_exist(language):
    catalog = load(language)
    required = [
        "bot.start.greeting",
        "bot.start.open_app",
        "bot.start.my_orders",
        "bot.start.language",
        "bot.start.language_prompt",
        "bot.start.help",
        "bot.help.text",
        "bot.help.commands",
        "bot.help.support",
        "bot.help.no_support",
        "bot.orders.empty",
        "bot.orders.title",
        "bot.orders.line",
        "bot.app_unavailable",
        "bot.unknown_command",
        "bot.blocked",
        "bot.menu.title",
        "lang.changed",
        "payment.description",
        "payment.created",
        "payment.pay_button",
        "payment.pending",
        "payment.succeeded",
        "payment.failed",
        "payment.expired",
        "payment.refunded",
        "payment.unavailable",
    ]
    assert [key for key in required if key not in catalog] == []
