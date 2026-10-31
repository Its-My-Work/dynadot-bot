"""Bot command and callback handlers (/start, /help, /lang, orders).

Every user-visible string goes through the injected ``t`` translator, so RU/EN
is decided once by the middleware rather than at each call site.
"""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.keyboards import (
    LANGUAGE_CALLBACK_PREFIX,
    domain_search_results,
    language_menu,
    main_menu,
    web_app_supported,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_session_factory
from app.i18n import Translator, available_languages
from app.integrations.dynadot import DynadotClient
from app.models.enums import Language
from app.models.order import Order
from app.models.user import User
from app.services.pricing import PricingService

logger = get_logger(__name__)

#: Module-level router kept for backwards compatibility and direct imports.
#: Prefer :func:`build_router` when constructing a dispatcher — aiogram refuses
#: to attach one router to two dispatchers, so reusing this singleton breaks
#: any second dispatcher in the same process.
router = Router(name="main")

#: How many recent orders /orders shows — enough to be useful, short enough
#: to stay under Telegram's message length limit.
ORDER_PAGE_SIZE = 10


def _greeting(t: Translator, user: User | None, message: Message) -> str:
    name = (
        (user.first_name if user else None)
        or (message.from_user.first_name if message.from_user else None)
        or "👋"
    )
    text = t("bot.start.greeting", name=name)
    if not web_app_supported(settings.tma_url):
        # Be explicit instead of silently showing a menu with no CTA.
        text = f"{text}\n\n{t('bot.app_unavailable')}"
    return text


@router.message(CommandStart())
async def cmd_start(message: Message, t: Translator, user: User | None) -> None:
    """Entry point: greet the user and offer the TMA."""
    if user is not None and user.is_blocked:
        await message.answer(t("bot.blocked"))
        return

    await message.answer(
        _greeting(t, user, message),
        reply_markup=main_menu(t.language, settings.tma_url),
    )


@router.message(Command("help"))
async def cmd_help(message: Message, t: Translator) -> None:
    lines = [t("bot.help.text"), "", t("bot.help.commands")]
    lines.append(
        t("bot.help.support", username=settings.support_username)
        if settings.support_username
        else t("bot.help.no_support")
    )
    await message.answer("\n".join(lines))


@router.message(Command("lang", "language"))
async def cmd_language(message: Message, t: Translator) -> None:
    await message.answer(t("bot.start.language_prompt"), reply_markup=language_menu())


@router.message(Command("orders"))
async def cmd_orders(message: Message, t: Translator, user: User | None) -> None:
    await message.answer(await _render_orders(t, user))


@router.callback_query(F.data == "help")
async def cb_help(query: CallbackQuery, t: Translator) -> None:
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.answer(t("bot.help.text"))


@router.callback_query(F.data == "language")
async def cb_language(query: CallbackQuery, t: Translator) -> None:
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.answer(
            t("bot.start.language_prompt"), reply_markup=language_menu()
        )


@router.callback_query(F.data == "orders")
async def cb_orders(query: CallbackQuery, t: Translator, user: User | None) -> None:
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.answer(await _render_orders(t, user))


@router.callback_query(F.data.startswith(LANGUAGE_CALLBACK_PREFIX))
async def cb_set_language(query: CallbackQuery, user: User | None) -> None:
    """Persist the chosen language so bot and TMA stay in sync."""
    code = (query.data or "").removeprefix(LANGUAGE_CALLBACK_PREFIX)
    if code not in available_languages():
        await query.answer()
        return

    if user is not None:
        async with get_session_factory()() as session:
            # Re-fetch: `user` came from a session that is already closed.
            fresh = await session.scalar(select(User).where(User.id == user.id))
            if fresh is not None:
                fresh.language = Language(code)
                await session.commit()

    t = Translator(code)
    await query.answer(t("lang.changed"))
    if isinstance(query.message, Message):
        await query.message.answer(
            t("lang.changed"), reply_markup=main_menu(code, settings.tma_url)
        )


async def _render_orders(t: Translator, user: User | None) -> str:
    """Render the user's most recent orders as a localised list."""
    if user is None:
        return t("bot.orders.empty")

    async with get_session_factory()() as session:
        orders = (
            await session.scalars(
                select(Order)
                .where(Order.user_id == user.id)
                .order_by(Order.created_at.desc())
                .limit(ORDER_PAGE_SIZE)
            )
        ).all()

    if not orders:
        return t("bot.orders.empty")

    lines = [t("bot.orders.title")]
    for order in orders:
        lines.append(
            t(
                "bot.orders.line",
                domain=order.domain_name,
                status=t(f"status.{order.status.value}"),
            )
        )
    return "\n".join(lines)


# Text search handler for direct query in chat
@router.message(F.text & ~F.text.startswith("/"))
async def msg_domain_search(message: Message, bot: Bot, t: Translator) -> None:
    raw_query = (message.text or "").strip().lower()
    clean = raw_query.lstrip(".").replace(" ", "")
    if not clean or len(clean) > 63:
        return

    # Check popular zones for telegram output
    tlds = ["com", "xyz", "org", "net", "io", "ai", "store", "pro"]
    if "." in clean:
        candidates = [clean]
        domain_base = clean.split(".")[0]
    else:
        candidates = [f"{clean}.{tld}" for tld in tlds]
        domain_base = clean

    wait_msg = await message.answer(f"🔍 Проверяем зоны для «{clean}»..." if t.language == "ru" else f"🔍 Searching zones for '{clean}'...")

    try:
        from app.integrations.dynadot.client import DynadotClient
        dynadot = DynadotClient(settings)
        try:
            raw = await dynadot.bulk_search(candidates)
            items = raw.get("domain_result_list") or []
        finally:
            await dynadot.aclose()

        async with get_session_factory()() as session:
            pricing_svc = PricingService(session, settings)
            parsed_results = []
            for item in items:
                domain_name = item.get("domain_name", "")
                d_tld = domain_name.rsplit(".", 1)[-1] if "." in domain_name else ""
                avail_raw = item.get("available")
                is_avail = avail_raw in (True, "yes", "true", "1")
                price_list = item.get("price_list") or []
                cost = 0.0
                if price_list and isinstance(price_list, list):
                    try:
                        cost = float(price_list[0].get("registration_price") or 0.0)
                    except (ValueError, TypeError):
                        cost = 0.0
                quote = await pricing_svc.quote(tld=d_tld, cost_usd=cost, years=1)
                parsed_results.append({
                    "domain": domain_name,
                    "tld": d_tld,
                    "available": is_avail,
                    "price_rub": quote.price_rub,
                })

        # Format message
        lines = [f"🔍 <b>Результаты для {domain_base}</b>\n" if t.language == "ru" else f"🔍 <b>Search results for {domain_base}</b>\n"]
        for res in parsed_results:
            d_name = res["domain"]
            if res["available"]:
                p_rub = f"{int(res['price_rub']):,} ₽".replace(",", " ")
                lines.append(f"🟢 <b>{d_name}</b> — <b>{p_rub}</b>")
            else:
                lines.append(f"❌ <s>{d_name}</s> — занят" if t.language == "ru" else f"❌ <s>{d_name}</s> — taken")

        text = "\n".join(lines)
        kb = domain_search_results(
            domain_results=parsed_results,
            query=domain_base,
            tma_url=settings.tma_url,
            language=t.language,
        )
        await bot.edit_message_text(
            text=text,
            chat_id=message.chat.id,
            message_id=wait_msg.message_id,
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception as exc:
        logger.warning("telegram text domain search failed", extra={"error": str(exc)})
        await bot.edit_message_text(
            text="Не удалось проверить домен прямо сейчас. Попробуйте в приложении:"
            if t.language == "ru"
            else "Failed to search domains. Try via the app:",
            chat_id=message.chat.id,
            message_id=wait_msg.message_id,
            reply_markup=main_menu(t.language, settings.tma_url),
        )


# Registered last: an earlier catch-all would shadow every command above.
@router.message(F.text.startswith("/"))
async def cmd_unknown(message: Message, t: Translator) -> None:
    await message.answer(t("bot.unknown_command"))


def build_router() -> Router:
    """Return a fresh router with every handler registered."""
    fresh = Router(name="main")

    fresh.message.register(cmd_start, CommandStart())
    fresh.message.register(cmd_help, Command("help"))
    fresh.message.register(cmd_language, Command("lang", "language"))
    fresh.message.register(cmd_orders, Command("orders"))

    fresh.callback_query.register(cb_help, F.data == "help")
    fresh.callback_query.register(cb_language, F.data == "language")
    fresh.callback_query.register(cb_orders, F.data == "orders")
    fresh.callback_query.register(
        cb_set_language, F.data.startswith(LANGUAGE_CALLBACK_PREFIX)
    )

    fresh.message.register(cmd_unknown, F.text.startswith("/"))
    fresh.message.register(msg_domain_search, F.text & ~F.text.startswith("/"))
    return fresh
