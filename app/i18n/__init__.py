"""Internationalisation (RU/EN) public API."""

from __future__ import annotations

from app.i18n.translator import (
    DEFAULT_LANGUAGE,
    Translator,
    available_languages,
    get_translator,
    normalize_language,
    parse_accept_language,
    translate,
)

__all__ = [
    "DEFAULT_LANGUAGE",
    "Translator",
    "available_languages",
    "get_translator",
    "normalize_language",
    "parse_accept_language",
    "translate",
]
