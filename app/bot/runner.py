"""Bot and Dispatcher construction plus the polling entrypoint.

Two run modes share one dispatcher:

* **Polling** (`python -m app.bot`) for local development.
* **Webhook** — `app.main` registers the URL on startup and
  `/api/v1/webhooks/telegram` feeds updates into this same dispatcher.
"""

from __future__ import annotations

import asyncio
import contextlib

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from app.bot.handlers import build_router
from app.bot.middlewares import UserContextMiddleware
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.i18n import translate

logger = get_logger(__name__)


class BotNotConfiguredError(RuntimeError):
    """Raised when TELEGRAM_BOT_TOKEN is missing."""


def create_bot(settings: Settings | None = None) -> Bot:
    settings = settings or get_settings()
    if not settings.telegram_bot_token:
        raise BotNotConfiguredError("TELEGRAM_BOT_TOKEN is not configured")
    return Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(settings: Settings | None = None) -> Dispatcher:
    """Build the dispatcher with middlewares attached.

    The middleware is registered for both messages and callback queries;
    attaching it to only one would leave half the handlers without a
    translator and blow up at runtime.
    """
    settings = settings or get_settings()
    dispatcher = Dispatcher()

    middleware = UserContextMiddleware()
    dispatcher.message.middleware(middleware)
    dispatcher.callback_query.middleware(middleware)

    # A fresh router per dispatcher: aiogram refuses to attach one router to
    # two dispatchers, which would break any second dispatcher in-process.
    dispatcher.include_router(build_router())
    return dispatcher


async def set_bot_commands(bot: Bot, settings: Settings | None = None) -> None:
    """Publish the command menu in every supported language."""
    settings = settings or get_settings()
    for language in settings.supported_languages:
        commands = [
            BotCommand(
                command="start", description=translate("bot.menu.title", language)
            ),
            BotCommand(
                command="orders",
                description=translate("bot.start.my_orders", language),
            ),
            BotCommand(
                command="lang", description=translate("bot.start.language", language)
            ),
            BotCommand(
                command="help", description=translate("bot.start.help", language)
            ),
        ]
        with contextlib.suppress(Exception):
            # A failed menu update must never stop the bot from starting.
            await bot.set_my_commands(commands, language_code=language)


async def run_polling(settings: Settings | None = None) -> None:
    """Long-polling entrypoint for local development."""
    settings = settings or get_settings()
    configure_logging()

    bot = create_bot(settings)
    dispatcher = create_dispatcher(settings)
    await set_bot_commands(bot, settings)

    # Drop any webhook first: Telegram refuses getUpdates while one is set.
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("bot polling started")
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("bot polling stopped")


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_polling())


if __name__ == "__main__":
    main()
