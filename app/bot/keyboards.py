"""Keyboards for the Telegram bot.

The primary CTA is a `WebAppInfo` button opening the TMA. Telegram only
accepts HTTPS URLs there, so `main_menu` degrades to a plain keyboard when
`TMA_URL` is unset or not HTTPS — otherwise every /start would fail with a
Bad Request.
"""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)

from app.i18n import translate

LANGUAGE_CALLBACK_PREFIX = "lang:"


def web_app_supported(url: str | None) -> bool:
    """Telegram rejects non-HTTPS web-app URLs outright."""
    return bool(url) and str(url).startswith("https://")


def main_menu(language: str, tma_url: str | None) -> InlineKeyboardMarkup:
    """Main menu: open the TMA, list orders, switch language."""
    rows: list[list[InlineKeyboardButton]] = []

    if web_app_supported(tma_url):
        rows.append(
            [
                InlineKeyboardButton(
                    text=translate("bot.start.open_app", language),
                    web_app=WebAppInfo(url=str(tma_url)),
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=translate("bot.start.my_orders", language),
                callback_data="orders",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=translate("bot.start.language", language),
                callback_data="language",
            ),
            InlineKeyboardButton(
                text=translate("bot.start.help", language), callback_data="help"
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def language_menu() -> InlineKeyboardMarkup:
    """Language picker. Untranslated on purpose: each option is in its own
    language, so a user who cannot read the current one can still escape."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🇷🇺 Русский", callback_data=f"{LANGUAGE_CALLBACK_PREFIX}ru"
                ),
                InlineKeyboardButton(
                    text="🇬🇧 English", callback_data=f"{LANGUAGE_CALLBACK_PREFIX}en"
                ),
            ]
        ]
    )


def payment_keyboard(
    language: str, payment_url: str, amount: float
) -> InlineKeyboardMarkup:
    """A single link button to the gateway's payment page."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=translate(
                        "payment.pay_button", language, amount=f"{amount:.0f}"
                    ),
                    url=payment_url,
                )
            ]
        ]
    )


def domain_search_results(
    domain_results: list[dict],
    query: str,
    tma_url: str | None,
    language: str,
) -> InlineKeyboardMarkup:
    """Inline keyboard for domain search results in Telegram."""
    rows: list[list[InlineKeyboardButton]] = []

    buy_buttons: list[InlineKeyboardButton] = []
    for item in domain_results:
        if item.get("available") and item.get("price_rub"):
            domain = item["domain"]
            price = int(item["price_rub"])
            tld = item.get("tld", "")
            btn_text = f"🛒 Купить .{tld} ({price} ₽)" if language == "ru" else f"🛒 Buy .{tld} ({price} ₽)"
            target_url = f"{tma_url}?domain={domain}" if tma_url else "https://dynadot.itsmywork.vip"
            if web_app_supported(target_url):
                buy_buttons.append(
                    InlineKeyboardButton(
                        text=btn_text,
                        web_app=WebAppInfo(url=target_url),
                    )
                )
            else:
                buy_buttons.append(
                    InlineKeyboardButton(
                        text=btn_text,
                        url=target_url,
                    )
                )
            if len(buy_buttons) >= 3:
                break

    for b in buy_buttons:
        rows.append([b])

    all_zones_url = f"{tma_url}?q={query}" if tma_url else "https://dynadot.itsmywork.vip"
    btn_all_text = (
        f"🌐 Все зоны и цены для «{query}»"
        if language == "ru"
        else f"🌐 All zones for '{query}'"
    )
    if web_app_supported(all_zones_url):
        rows.append(
            [InlineKeyboardButton(text=btn_all_text, web_app=WebAppInfo(url=all_zones_url))]
        )
    else:
        rows.append(
            [InlineKeyboardButton(text=btn_all_text, url=all_zones_url)]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)
