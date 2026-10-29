"""Translation catalogue with RU/EN support and graceful fallbacks."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

LOCALES_DIR = Path(__file__).parent / "locales"
DEFAULT_LANGUAGE = "ru"


@lru_cache
def _load_catalog(language: str) -> dict[str, str]:
    path = LOCALES_DIR / f"{language}.json"
    if not path.exists():
        logger.warning("locale file missing", extra={"language": language})
        return {}
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def available_languages() -> list[str]:
    """Languages that both have a catalogue file and are enabled in config."""
    on_disk = {p.stem for p in LOCALES_DIR.glob("*.json")}
    return [lang for lang in settings.supported_languages if lang in on_disk]


def normalize_language(language: str | None) -> str:
    """Map an arbitrary locale tag (e.g. ``ru-RU``, ``EN``) onto a supported one."""
    if not language:
        return settings.default_language
    tag = language.strip().lower().replace("_", "-").split("-")[0]
    if tag in available_languages():
        return tag
    return settings.default_language


def parse_accept_language(header: str | None) -> str:
    """Pick the best supported language from an ``Accept-Language`` header."""
    if not header:
        return settings.default_language

    candidates: list[tuple[float, str]] = []
    for part in header.split(","):
        bits = part.split(";q=")
        tag = bits[0].strip().lower().split("-")[0]
        try:
            quality = float(bits[1]) if len(bits) > 1 else 1.0
        except ValueError:
            quality = 1.0
        if tag:
            candidates.append((quality, tag))

    supported = available_languages()
    for _, tag in sorted(candidates, key=lambda item: item[0], reverse=True):
        if tag in supported:
            return tag
    return settings.default_language


def translate(key: str, language: str | None = None, /, **params: object) -> str:
    """Return the localised string for ``key``.

    Resolution order: requested language -> default language -> the key itself.
    A missing interpolation placeholder never raises; the raw template is
    returned instead so a bad translation cannot break a request.
    """
    lang = normalize_language(language)
    template = _load_catalog(lang).get(key)

    if template is None and lang != DEFAULT_LANGUAGE:
        template = _load_catalog(DEFAULT_LANGUAGE).get(key)

    if template is None:
        logger.warning("translation missing", extra={"key": key, "language": lang})
        return key

    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        logger.warning(
            "translation interpolation failed", extra={"key": key, "language": lang}
        )
        return template


class Translator:
    """Small bound helper so call sites don't repeat the language argument."""

    __slots__ = ("language",)

    def __init__(self, language: str | None = None) -> None:
        self.language = normalize_language(language)

    def __call__(self, key: str, **params: object) -> str:
        return translate(key, self.language, **params)

    gettext = __call__


def get_translator(language: str | None = None) -> Translator:
    return Translator(language)
