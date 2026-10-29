"""Tests for the aiogram bot: handlers, keyboards, localisation, notifications."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from aiogram.types import InlineKeyboardMarkup
from aiogram.types import User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.handlers import (
    _render_orders,
    cb_set_language,
    cmd_help,
    cmd_language,
    cmd_start,
    cmd_unknown,
)
from app.bot.keyboards import (
    LANGUAGE_CALLBACK_PREFIX,
    language_menu,
    main_menu,
    payment_keyboard,
    web_app_supported,
)
from app.bot.middlewares import UserContextMiddleware
from app.bot.notifications import (
    notify_order_status,
    notify_payment_created,
    notify_payment_status,
    safe_send,
)
from app.bot.runner import BotNotConfiguredError, create_bot, create_dispatcher
from app.core.config import Settings
from app.i18n import Translator
from app.models import Base
from app.models.enums import Language, OrderStatus, PaymentStatus
from app.models.order import Order
from app.models.user import User

TMA_URL = "https://tma.example.test"


# ----------------------------------------------------------------- keyboards


def test_web_app_supported_requires_https():
    """Telegram rejects non-HTTPS web-app URLs with a Bad Request."""
    assert web_app_supported("https://ok.test") is True
    assert web_app_supported("http://insecure.test") is False
    assert web_app_supported(None) is False


def test_main_menu_includes_the_web_app_button_when_url_is_https():
    markup = main_menu("ru", TMA_URL)
    assert isinstance(markup, InlineKeyboardMarkup)
    web_app_buttons = [
        b for row in markup.inline_keyboard for b in row if b.web_app is not None
    ]
    assert len(web_app_buttons) == 1
    assert web_app_buttons[0].web_app.url == TMA_URL


def test_main_menu_degrades_gracefully_without_a_usable_tma_url():
    """A web_app button with a bad URL would make every /start fail."""
    markup = main_menu("ru", "http://insecure.test")
    assert all(b.web_app is None for row in markup.inline_keyboard for b in row)
    # The rest of the menu must still be usable.
    assert any(b.callback_data == "orders" for row in markup.inline_keyboard for b in row)


@pytest.mark.parametrize("language", ["ru", "en"])
def test_main_menu_is_localised(language):
    markup = main_menu(language, TMA_URL)
    texts = [b.text for row in markup.inline_keyboard for b in row]
    expected = Translator(language)("bot.start.my_orders")
    assert expected in texts


def test_main_menu_text_differs_between_ru_and_en():
    ru = [b.text for row in main_menu("ru", TMA_URL).inline_keyboard for b in row]
    en = [b.text for row in main_menu("en", TMA_URL).inline_keyboard for b in row]
    assert ru != en


def test_language_menu_offers_both_languages_in_their_own_script():
    markup = language_menu()
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert {b.callback_data for b in buttons} == {
        f"{LANGUAGE_CALLBACK_PREFIX}ru",
        f"{LANGUAGE_CALLBACK_PREFIX}en",
    }
    assert any("Русский" in b.text for b in buttons)
    assert any("English" in b.text for b in buttons)


def test_payment_keyboard_links_to_the_gateway():
    markup = payment_keyboard("ru", "https://pay.test/abc", 1187.5)
    button = markup.inline_keyboard[0][0]
    assert button.url == "https://pay.test/abc"
    assert "1188" in button.text or "1187" in button.text


# -------------------------------------------------------------------- runner


def test_create_bot_without_a_token_raises():
    with pytest.raises(BotNotConfiguredError):
        create_bot(Settings(telegram_bot_token=None))


def test_create_bot_with_a_token_succeeds():
    bot = create_bot(Settings(telegram_bot_token="123456:test-token"))
    assert bot.token == "123456:test-token"


def test_dispatcher_registers_middleware_on_messages_and_callbacks():
    """Attaching it to only one would leave half the handlers translator-less."""
    dispatcher = create_dispatcher(Settings())
    assert any(
        isinstance(m, UserContextMiddleware)
        for m in dispatcher.message.middleware._middlewares
    )
    assert any(
        isinstance(m, UserContextMiddleware)
        for m in dispatcher.callback_query.middleware._middlewares
    )


def test_two_dispatchers_can_coexist_in_one_process():
    """aiogram refuses to attach one router twice, so each needs a fresh one."""
    first = create_dispatcher(Settings())
    second = create_dispatcher(Settings())
    assert first is not second


def test_build_router_registers_every_handler():
    from app.bot.handlers import build_router

    built = build_router()
    assert len(built.message.handlers) == 5
    assert len(built.callback_query.handlers) == 4


# ------------------------------------------------------------------ fixtures


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bot.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


class FakeMessage:
    """Captures what a handler would send instead of calling Telegram."""

    def __init__(self, user: TgUser | None = None) -> None:
        self.from_user = user or TgUser(
            id=555001, is_bot=False, first_name="Ann", language_code="ru"
        )
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text: str, reply_markup: object = None, **kwargs) -> None:
        self.answers.append((text, reply_markup))


class FakeQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.answers: list[str | None] = []
        self.message = None

    async def answer(self, text: str | None = None, **kwargs) -> None:
        self.answers.append(text)


# ---------------------------------------------------------------- middleware


@pytest.mark.asyncio
async def test_middleware_creates_the_user_and_injects_the_translator(
    session_factory,
):
    middleware = UserContextMiddleware(session_factory)
    captured: dict = {}

    async def handler(event, data):
        captured.update(data)
        return "done"

    tg_user = TgUser(id=900001, is_bot=False, first_name="Nina", language_code="en")
    result = await middleware(handler, object(), {"event_from_user": tg_user})

    assert result == "done"
    assert captured["user"].telegram_id == 900001
    # Language is seeded from the Telegram client locale.
    assert captured["language"] == "en"
    assert isinstance(captured["t"], Translator)
    assert captured["t"].language == "en"


@pytest.mark.asyncio
async def test_middleware_seeds_russian_for_an_unsupported_locale(session_factory):
    middleware = UserContextMiddleware(session_factory)
    captured: dict = {}

    async def handler(event, data):
        captured.update(data)

    tg_user = TgUser(id=900002, is_bot=False, first_name="Jin", language_code="zh")
    await middleware(handler, object(), {"event_from_user": tg_user})
    assert captured["language"] == "ru"


@pytest.mark.asyncio
async def test_middleware_never_overwrites_a_chosen_language(session_factory):
    """A user who picked EN must keep it even though their client reports RU."""
    async with session_factory() as session:
        session.add(User(telegram_id=900003, first_name="Old", language=Language.EN))
        await session.commit()

    middleware = UserContextMiddleware(session_factory)
    captured: dict = {}

    async def handler(event, data):
        captured.update(data)

    tg_user = TgUser(id=900003, is_bot=False, first_name="New", language_code="ru")
    await middleware(handler, object(), {"event_from_user": tg_user})

    assert captured["language"] == "en"
    # Profile fields are still refreshed.
    assert captured["user"].first_name == "New"


@pytest.mark.asyncio
async def test_middleware_updates_last_seen(session_factory):
    middleware = UserContextMiddleware(session_factory)

    async def handler(event, data):
        return data["user"]

    tg_user = TgUser(id=900004, is_bot=False, first_name="Seen")
    user = await middleware(handler, object(), {"event_from_user": tg_user})
    assert user.last_seen_at is not None


@pytest.mark.asyncio
async def test_middleware_skips_db_work_for_bot_senders(session_factory):
    middleware = UserContextMiddleware(session_factory)
    captured: dict = {}

    async def handler(event, data):
        captured.update(data)

    bot_user = TgUser(id=1, is_bot=True, first_name="SomeBot")
    await middleware(handler, object(), {"event_from_user": bot_user})
    assert captured["user"] is None


@pytest.mark.asyncio
async def test_middleware_handles_an_update_with_no_sender(session_factory):
    middleware = UserContextMiddleware(session_factory)
    captured: dict = {}

    async def handler(event, data):
        captured.update(data)

    await middleware(handler, object(), {})
    assert captured["user"] is None
    assert captured["language"] == "ru"


# ------------------------------------------------------------------ handlers


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["ru", "en"])
async def test_start_greets_in_the_users_language(monkeypatch, language):
    import app.bot.handlers as handlers_module

    monkeypatch.setattr(handlers_module.settings, "tma_url", TMA_URL, raising=False)

    message = FakeMessage()
    user = User(telegram_id=555001, first_name="Ann", language=Language(language))
    await cmd_start(message, Translator(language), user)

    text, markup = message.answers[0]
    assert "Ann" in text
    assert isinstance(markup, InlineKeyboardMarkup)
    if language == "ru":
        assert "Привет" in text
    else:
        assert "Hi" in text


@pytest.mark.asyncio
async def test_start_warns_when_the_tma_url_is_unusable(monkeypatch):
    import app.bot.handlers as handlers_module

    monkeypatch.setattr(handlers_module.settings, "tma_url", None, raising=False)

    message = FakeMessage()
    await cmd_start(message, Translator("ru"), None)
    text, _ = message.answers[0]
    assert Translator("ru")("bot.app_unavailable") in text


@pytest.mark.asyncio
async def test_start_refuses_a_blocked_user():
    message = FakeMessage()
    user = User(telegram_id=555002, first_name="Bad", is_blocked=True)
    await cmd_start(message, Translator("ru"), user)

    text, markup = message.answers[0]
    assert text == Translator("ru")("bot.blocked")
    # No menu for a blocked account.
    assert markup is None


@pytest.mark.asyncio
async def test_help_lists_commands_and_support(monkeypatch):
    import app.bot.handlers as handlers_module

    monkeypatch.setattr(
        handlers_module.settings, "support_username", "@help", raising=False
    )
    message = FakeMessage()
    await cmd_help(message, Translator("en"))

    text = message.answers[0][0]
    assert "/start" in text
    assert "@help" in text


@pytest.mark.asyncio
async def test_help_says_so_when_support_is_unset(monkeypatch):
    import app.bot.handlers as handlers_module

    monkeypatch.setattr(handlers_module.settings, "support_username", None, raising=False)
    message = FakeMessage()
    await cmd_help(message, Translator("ru"))
    assert Translator("ru")("bot.help.no_support") in message.answers[0][0]


@pytest.mark.asyncio
async def test_language_command_shows_the_picker():
    message = FakeMessage()
    await cmd_language(message, Translator("ru"))
    _, markup = message.answers[0]
    assert isinstance(markup, InlineKeyboardMarkup)


@pytest.mark.asyncio
async def test_unknown_command_is_answered_politely():
    message = FakeMessage()
    await cmd_unknown(message, Translator("en"))
    assert message.answers[0][0] == Translator("en")("bot.unknown_command")


# ------------------------------------------------------- language switching


@pytest.mark.asyncio
async def test_language_callback_persists_the_choice(session_factory, monkeypatch):
    import app.bot.handlers as handlers_module
    import app.db.session as session_module

    monkeypatch.setattr(session_module, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(handlers_module, "get_session_factory", lambda: session_factory)

    async with session_factory() as session:
        user = User(telegram_id=910001, first_name="Switcher", language=Language.RU)
        session.add(user)
        await session.commit()
        await session.refresh(user)

    query = FakeQuery(f"{LANGUAGE_CALLBACK_PREFIX}en")
    await cb_set_language(query, user)

    async with session_factory() as session:
        fresh = await session.scalar(select(User).where(User.telegram_id == 910001))
        assert fresh.language == Language.EN

    # The confirmation itself arrives in the newly chosen language.
    assert query.answers[0] == Translator("en")("lang.changed")


@pytest.mark.asyncio
async def test_language_callback_ignores_an_unsupported_code(
    session_factory, monkeypatch
):
    import app.bot.handlers as handlers_module

    monkeypatch.setattr(handlers_module, "get_session_factory", lambda: session_factory)

    async with session_factory() as session:
        user = User(telegram_id=910002, first_name="X", language=Language.RU)
        session.add(user)
        await session.commit()
        await session.refresh(user)

    await cb_set_language(FakeQuery(f"{LANGUAGE_CALLBACK_PREFIX}de"), user)

    async with session_factory() as session:
        fresh = await session.scalar(select(User).where(User.telegram_id == 910002))
        assert fresh.language == Language.RU


# -------------------------------------------------------------------- orders


@pytest.mark.asyncio
async def test_orders_list_is_empty_for_a_new_user(session_factory, monkeypatch):
    import app.bot.handlers as handlers_module

    monkeypatch.setattr(handlers_module, "get_session_factory", lambda: session_factory)
    async with session_factory() as session:
        user = User(telegram_id=920001, first_name="Fresh")
        session.add(user)
        await session.commit()
        await session.refresh(user)

    text = await _render_orders(Translator("ru"), user)
    assert text == Translator("ru")("bot.orders.empty")


@pytest.mark.asyncio
async def test_orders_list_shows_localised_statuses(session_factory, monkeypatch):
    import app.bot.handlers as handlers_module

    monkeypatch.setattr(handlers_module, "get_session_factory", lambda: session_factory)
    async with session_factory() as session:
        user = User(telegram_id=920002, first_name="Buyer")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        session.add(
            Order(
                user_id=user.id,
                domain_name="listed.com",
                tld="com",
                years=1,
                status=OrderStatus.AWAITING_PAYMENT,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    ru = await _render_orders(Translator("ru"), user)
    en = await _render_orders(Translator("en"), user)

    assert "listed.com" in ru
    assert Translator("ru")("status.awaiting_payment") in ru
    assert Translator("en")("status.awaiting_payment") in en
    assert ru != en


@pytest.mark.asyncio
async def test_orders_list_handles_a_missing_user():
    assert await _render_orders(Translator("ru"), None) == Translator("ru")(
        "bot.orders.empty"
    )


# ------------------------------------------------------------- notifications


class FakeBot:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail = fail

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        if self.fail:
            from aiogram.exceptions import TelegramForbiddenError

            raise TelegramForbiddenError(method=None, message="bot was blocked")
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})


@pytest.mark.asyncio
async def test_safe_send_delivers_a_message():
    bot = FakeBot()
    assert await safe_send(bot, 111, "hello") is True
    assert bot.sent[0]["text"] == "hello"


@pytest.mark.asyncio
async def test_safe_send_swallows_a_blocked_bot():
    """A blocked user must not fail a webhook and trigger a gateway retry."""
    bot = FakeBot(fail=True)
    assert await safe_send(bot, 111, "hello") is False


@pytest.mark.asyncio
async def test_payment_created_notification_carries_the_pay_button():
    bot = FakeBot()
    await notify_payment_created(
        bot, 222, language="ru", amount=1187.5, payment_url="https://pay.test/x"
    )
    sent = bot.sent[0]
    assert "1188" in sent["text"] or "1187" in sent["text"]
    assert sent["reply_markup"].inline_keyboard[0][0].url == "https://pay.test/x"


@pytest.mark.asyncio
async def test_payment_created_notification_without_a_url_has_no_markup():
    bot = FakeBot()
    await notify_payment_created(bot, 222, language="en", amount=100, payment_url=None)
    assert bot.sent[0]["reply_markup"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["ru", "en"])
async def test_payment_status_notification_is_localised(language):
    bot = FakeBot()
    await notify_payment_status(
        bot,
        333,
        language=language,
        status=PaymentStatus.SUCCEEDED,
        domain="paid.com",
    )
    assert "paid.com" in bot.sent[0]["text"]


@pytest.mark.asyncio
async def test_order_status_notification_uses_the_order_locale_key():
    bot = FakeBot()
    sent = await notify_order_status(
        bot, 444, language="en", status=OrderStatus.COMPLETED, domain="done.com"
    )
    assert sent is True
    assert "done.com" in bot.sent[0]["text"]


@pytest.mark.asyncio
async def test_order_status_without_a_message_sends_nothing():
    bot = FakeBot()
    sent = await notify_order_status(
        bot, 444, language="ru", status=OrderStatus.DRAFT, domain="d.com"
    )
    assert sent is False
    assert bot.sent == []
