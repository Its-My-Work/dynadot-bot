"""Dispatcher-level routing tests.

The unit tests in ``test_bot.py`` call handlers directly, which proves the
logic but not the *filters*. These tests feed real ``Update`` objects through
the real dispatcher, so a broken ``CommandStart()`` filter or a catch-all that
shadows every command would be caught here.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.handlers import build_router
from app.bot.middlewares import UserContextMiddleware
from app.i18n import Translator
from app.models import Base
from app.models.user import User

TOKEN = "123456:AAHtest-token-value-placeholder"


class RecordingSession(BaseSession):
    """Captures outbound API calls instead of hitting Telegram."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[object] = []

    async def close(self) -> None:
        return None

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        return Message(
            message_id=999,
            date=datetime.now(UTC),
            chat=Chat(id=getattr(method, "chat_id", 700001), type="private"),
            text=getattr(method, "text", ""),
        )

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        yield b""

    @property
    def sent_texts(self) -> list[str]:
        texts: list[str] = []
        for m in self.requests:
            if hasattr(m, "text"):
                texts.append(m.text)
        return texts


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/dispatch.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def harness(session_factory, monkeypatch):
    """A real Dispatcher with a recording transport and an isolated DB."""
    from aiogram import Dispatcher

    import app.bot.handlers as handlers_module

    monkeypatch.setattr(handlers_module, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(
        handlers_module.settings, "tma_url", "https://tma.test", raising=False
    )

    recording = RecordingSession()
    bot = Bot(token=TOKEN, session=recording)

    dispatcher = Dispatcher()
    middleware = UserContextMiddleware(session_factory)
    dispatcher.message.middleware(middleware)
    dispatcher.callback_query.middleware(middleware)
    dispatcher.include_router(build_router())

    yield bot, dispatcher, recording, session_factory
    await bot.session.close()


def make_update(text: str, *, user_id: int = 700001, language_code: str = "ru"):
    from aiogram.types import User as TgUser

    return Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=user_id, type="private"),
            from_user=TgUser(
                id=user_id,
                is_bot=False,
                first_name="Dima",
                language_code=language_code,
            ),
            text=text,
        ),
    )


@pytest.mark.asyncio
async def test_start_command_routes_and_creates_the_user(harness):
    bot, dispatcher, recording, factory = harness

    await dispatcher.feed_update(bot, make_update("/start"))

    assert len(recording.sent_texts) == 1
    assert "Dima" in recording.sent_texts[0]

    # The middleware persisted the user as a side effect of the update.
    async with factory() as session:
        from sqlalchemy import select

        user = await session.scalar(select(User).where(User.telegram_id == 700001))
        assert user is not None


@pytest.mark.asyncio
async def test_start_replies_in_english_for_an_english_client(harness):
    bot, dispatcher, recording, _ = harness

    await dispatcher.feed_update(
        bot, make_update("/start", user_id=700002, language_code="en")
    )
    assert "Hi" in recording.sent_texts[0]


@pytest.mark.asyncio
async def test_help_command_routes_to_the_help_handler(harness):
    bot, dispatcher, recording, _ = harness

    await dispatcher.feed_update(bot, make_update("/help"))
    assert Translator("ru")("bot.help.text") in recording.sent_texts[0]


@pytest.mark.asyncio
async def test_lang_command_sends_the_language_keyboard(harness):
    bot, dispatcher, recording, _ = harness

    await dispatcher.feed_update(bot, make_update("/lang"))
    send = recording.requests[0]
    assert send.reply_markup is not None
    codes = {b.callback_data for row in send.reply_markup.inline_keyboard for b in row}
    assert codes == {"lang:ru", "lang:en"}


@pytest.mark.asyncio
async def test_orders_command_reports_an_empty_list(harness):
    bot, dispatcher, recording, _ = harness

    await dispatcher.feed_update(bot, make_update("/orders"))
    assert recording.sent_texts[0] == Translator("ru")("bot.orders.empty")


@pytest.mark.asyncio
async def test_unknown_command_falls_through_to_the_catch_all(harness):
    bot, dispatcher, recording, _ = harness

    await dispatcher.feed_update(bot, make_update("/definitely_not_a_command"))
    assert recording.sent_texts[0] == Translator("ru")("bot.unknown_command")


@pytest.mark.asyncio
async def test_the_catch_all_does_not_shadow_real_commands(harness):
    """Registration order matters: /start must never hit the catch-all."""
    bot, dispatcher, recording, _ = harness

    await dispatcher.feed_update(bot, make_update("/start"))
    assert recording.sent_texts[0] != Translator("ru")("bot.unknown_command")


@pytest.mark.asyncio
async def test_plain_text_triggers_domain_search_not_catch_all(harness, monkeypatch):
    """Plain text queries trigger domain search flow, not the / command catch-all."""
    bot, dispatcher, recording, _ = harness
    import app.integrations.dynadot.client as dyn_client_module

    async def fake_bulk_search(self, domains):
        return {"domain_result_list": [{"domain_name": d, "available": True} for d in domains]}

    async def fake_aclose(self):
        pass

    monkeypatch.setattr(dyn_client_module.DynadotClient, "bulk_search", fake_bulk_search)
    monkeypatch.setattr(dyn_client_module.DynadotClient, "aclose", fake_aclose)

    await dispatcher.feed_update(bot, make_update("mybeststartup"))
    assert len(recording.sent_texts) > 0
    assert Translator("ru")("bot.unknown_command") not in recording.sent_texts[0]


@pytest.mark.asyncio
async def test_language_callback_routes_and_persists(harness):
    bot, dispatcher, recording, factory = harness
    from aiogram.types import CallbackQuery
    from aiogram.types import User as TgUser
    from sqlalchemy import select

    # Create the user first via /start.
    await dispatcher.feed_update(bot, make_update("/start", user_id=700010))
    recording.requests.clear()

    tg_user = TgUser(id=700010, is_bot=False, first_name="Dima")
    update = Update(
        update_id=2,
        callback_query=CallbackQuery(
            id="cb-1",
            from_user=tg_user,
            chat_instance="ci-1",
            data="lang:en",
            message=Message(
                message_id=5,
                date=datetime.now(UTC),
                chat=Chat(id=700010, type="private"),
                text="menu",
            ),
        ),
    )
    await dispatcher.feed_update(bot, update)

    async with factory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == 700010))
        assert user.language.value == "en"
