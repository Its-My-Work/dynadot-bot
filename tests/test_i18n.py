"""Tests for the RU/EN translation layer."""

from __future__ import annotations

from app.i18n import (
    available_languages,
    normalize_language,
    parse_accept_language,
    translate,
)
from app.i18n.translator import _load_catalog


def test_both_languages_available():
    assert set(available_languages()) == {"ru", "en"}


def test_catalogs_have_identical_keys():
    """A key present in one language but missing in the other is a bug."""
    assert set(_load_catalog("ru")) == set(_load_catalog("en"))


def test_translate_returns_language_specific_text():
    assert translate("common.yes", "ru") == "Да"
    assert translate("common.yes", "en") == "Yes"


def test_translate_interpolates_params():
    assert "example.com" in translate("order.completed", "en", domain="example.com")
    assert "example.com" in translate("order.completed", "ru", domain="example.com")


def test_missing_key_returns_the_key_itself():
    assert translate("no.such.key", "en") == "no.such.key"


def test_bad_interpolation_returns_template_instead_of_raising():
    # Omitting the required {domain} placeholder must not blow up.
    result = translate("order.completed", "en")
    assert "{domain}" in result


def test_normalize_language_handles_region_tags_and_case():
    assert normalize_language("ru-RU") == "ru"
    assert normalize_language("EN") == "en"
    assert normalize_language("en_US") == "en"


def test_normalize_language_falls_back_for_unsupported():
    assert normalize_language("de") == "ru"
    assert normalize_language(None) == "ru"


def test_parse_accept_language_respects_quality_values():
    assert parse_accept_language("en-US,en;q=0.9") == "en"
    assert parse_accept_language("de;q=0.9,en;q=0.8") == "en"
    assert parse_accept_language("ru-RU,ru;q=0.9,en;q=0.5") == "ru"


def test_parse_accept_language_defaults_when_unsupported_or_empty():
    assert parse_accept_language(None) == "ru"
    assert parse_accept_language("zh-CN,ja;q=0.8") == "ru"
