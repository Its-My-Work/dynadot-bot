"""Telegram bot package (aiogram 3.x)."""

from __future__ import annotations

from app.bot.keyboards import language_menu, main_menu, payment_keyboard
from app.bot.middlewares import UserContextMiddleware
from app.bot.notifications import (
    notify_order_status,
    notify_payment_created,
    notify_payment_status,
    safe_send,
)
from app.bot.runner import (
    BotNotConfiguredError,
    create_bot,
    create_dispatcher,
    run_polling,
    set_bot_commands,
)

__all__ = [
    "BotNotConfiguredError",
    "UserContextMiddleware",
    "create_bot",
    "create_dispatcher",
    "language_menu",
    "main_menu",
    "notify_order_status",
    "notify_payment_created",
    "notify_payment_status",
    "payment_keyboard",
    "run_polling",
    "safe_send",
    "set_bot_commands",
]
