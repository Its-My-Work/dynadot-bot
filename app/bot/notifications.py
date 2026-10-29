"""Outbound Telegram notifications triggered by backend events.

Payment and registration outcomes arrive on the webhook thread, not in a bot
handler, so these helpers take an explicit ``Bot``. Every send is wrapped:
a user who blocked the bot must not fail the webhook and cause the gateway to
retry a correctly-processed payment.
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.bot.keyboards import payment_keyboard
from app.core.logging import get_logger
from app.i18n import translate
from app.models.enums import OrderStatus, PaymentStatus

logger = get_logger(__name__)

#: Payment status -> locale key for the customer-facing message.
_PAYMENT_MESSAGES: dict[PaymentStatus, str] = {
    PaymentStatus.PENDING: "payment.pending",
    PaymentStatus.SUCCEEDED: "payment.succeeded",
    PaymentStatus.FAILED: "payment.failed",
    PaymentStatus.EXPIRED: "payment.expired",
    PaymentStatus.REFUNDED: "payment.refunded",
}

_ORDER_MESSAGES: dict[OrderStatus, str] = {
    OrderStatus.PAID: "order.paid",
    OrderStatus.COMPLETED: "order.completed",
    OrderStatus.FAILED: "order.failed",
    OrderStatus.REFUND_REQUIRED: "order.refund_required",
    OrderStatus.REFUNDED: "payment.refunded",
}


async def safe_send(
    bot: Bot, chat_id: int, text: str, reply_markup: object | None = None
) -> bool:
    """Send a message, swallowing Telegram errors.

    Returns True on success. A blocked bot or deleted chat is an expected
    outcome, not an error worth failing the caller over.
    """
    try:
        await bot.send_message(chat_id, text, reply_markup=reply_markup)
        return True
    except TelegramAPIError as exc:
        logger.warning(
            "telegram send failed", extra={"chat_id": chat_id, "error": str(exc)}
        )
        return False


async def notify_payment_created(
    bot: Bot,
    telegram_id: int,
    *,
    language: str,
    amount: float,
    payment_url: str | None,
) -> bool:
    """Tell the customer an invoice exists and give them the pay button."""
    text = translate("payment.created", language, amount=f"{amount:.0f}")
    markup = payment_keyboard(language, payment_url, amount) if payment_url else None
    return await safe_send(bot, telegram_id, text, markup)


async def notify_payment_status(
    bot: Bot,
    telegram_id: int,
    *,
    language: str,
    status: PaymentStatus,
    domain: str | None = None,
) -> bool:
    """Report a payment status change in the user's language."""
    key = _PAYMENT_MESSAGES.get(status)
    if key is None:
        return False
    return await safe_send(
        bot, telegram_id, translate(key, language, domain=domain or "")
    )


async def notify_order_status(
    bot: Bot,
    telegram_id: int,
    *,
    language: str,
    status: OrderStatus,
    domain: str,
) -> bool:
    """Report an order status change in the user's language."""
    key = _ORDER_MESSAGES.get(status)
    if key is None:
        return False
    return await safe_send(bot, telegram_id, translate(key, language, domain=domain))
