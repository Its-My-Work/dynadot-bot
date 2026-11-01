"""Query normalization and layout/transliteration correction for domain searches."""

from __future__ import annotations

import re

# QWERTY to Cyrillic (ЙЦУКЕН)
_EN_TO_RU = str.maketrans(
    "qwertyuiop[]asdfghjkl;'zxcvbnm,./`"
    "QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>?~",
    "йцукенгшщзхъфывапролджэячсмитьбю.ё"
    "ЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,Ё",
)

# Cyrillic (ЙЦУКЕН) to QWERTY
_RU_TO_EN = str.maketrans(
    "йцукенгшщзхъфывапролджэячсмитьбю.ё"
    "ЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,Ё",
    "qwertyuiop[]asdfghjkl;'zxcvbnm,./`"
    "QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>?~",
)

# Common Russian phonetic transliteration to Latin
_TRANSLIT_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

# Known TLD typos in Russian layout
_RU_TLD_TYPOS = {
    "сщь": "com",
    "фш": "ai",
    "шщ": "io",
    "туе": "net",
    "щкп": "org",
    "ишя": "biz",
    "ызфсу": "space",
    "ыещку": "store",
    "ышеу": "site",
    "щтдшту": "online",
    "дуср": "tech",
}


def transliterate_cyrillic(text: str) -> str:
    """Convert Russian cyrillic characters to standard latin representation."""
    res: list[str] = []
    text_lower = text.lower()
    for ch in text_lower:
        if ch in _TRANSLIT_MAP:
            res.append(_TRANSLIT_MAP[ch])
        elif re.match(r"[a-z0-9\-.]", ch):
            res.append(ch)
    return "".join(res)


def ru_to_en_layout(text: str) -> str:
    """Convert Russian keyboard keystrokes to English QWERTY."""
    return text.translate(_RU_TO_EN)


def en_to_ru_layout(text: str) -> str:
    """Convert English keyboard keystrokes to Russian ЙЦУКЕН."""
    return text.translate(_EN_TO_RU)


def normalize_domain_query(raw_query: str) -> tuple[str, str | None]:
    """Normalize domain search query and detect layout or transliteration suggestions.

    Returns:
        (clean_query, suggested_query_or_none)
    """
    raw = raw_query.strip().lower()
    if not raw:
        return "", None

    # Replace Russian TLD endings if present, e.g. .сщь -> .com
    for ru_tld, en_tld in _RU_TLD_TYPOS.items():
        if raw.endswith("." + ru_tld):
            raw = raw[: -(len(ru_tld) + 1)] + "." + en_tld
        elif raw.endswith(ru_tld) and len(raw) > len(ru_tld):
            raw = raw[: -len(ru_tld)] + "." + en_tld

    # Check if text contains Cyrillic characters
    has_cyrillic = bool(re.search(r"[\u0400-\u04FF]", raw))

    if has_cyrillic:
        # User typed Russian layout (e.g. 'сщь' or 'москва')
        as_en_layout = ru_to_en_layout(raw)
        translit = transliterate_cyrillic(raw)

        # If it looks like transliteration (e.g. 'москва' -> 'moskva')
        if translit and translit != raw:
            clean = translit
            suggestion = as_en_layout if as_en_layout != clean else None
            return clean, suggestion

        return as_en_layout, None

    # If all latin: check if typing might be Russian words in English layout (e.g. 'ghbdtn' -> 'привет' -> 'privet')
    as_ru = en_to_ru_layout(raw)
    translit_of_ru = transliterate_cyrillic(as_ru)
    if translit_of_ru and translit_of_ru != raw:
        # Check if as_ru looks like a coherent word
        clean = re.sub(r"[^a-z0-9\-.]", "", raw)
        return clean, translit_of_ru

    clean = re.sub(r"[^a-z0-9\-.]", "", raw)
    return clean, None
