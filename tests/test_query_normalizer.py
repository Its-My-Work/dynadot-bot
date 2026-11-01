from app.services.query_normalizer import (
    en_to_ru_layout,
    normalize_domain_query,
    ru_to_en_layout,
    transliterate_cyrillic,
)


def test_keyboard_layouts():
    assert en_to_ru_layout("ghbdtn") == "привет"
    assert ru_to_en_layout("сщь") == "com"


def test_transliterate_cyrillic():
    assert transliterate_cyrillic("кофе") == "kofe"
    assert transliterate_cyrillic("доставка") == "dostavka"
    assert transliterate_cyrillic("москва") == "moskva"


def test_normalize_domain_query_russian_word():
    clean, _ = normalize_domain_query("кофешоп")
    assert clean == "kofeshop"


def test_normalize_domain_query_english_layout_typo():
    clean, suggestion = normalize_domain_query("ghbdtn")
    assert clean == "ghbdtn"
    assert suggestion == "privet"


def test_normalize_domain_query_valid_latin_no_false_suggestions():
    clean, suggestion = normalize_domain_query("itsmywork")
    assert clean == "itsmywork"
    assert suggestion is None


def test_normalize_domain_query_typo_tld():
    clean, _ = normalize_domain_query("mycafe.сщь")
    assert clean == "mycafe.com"
