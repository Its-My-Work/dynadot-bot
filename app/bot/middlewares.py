"""Aiogram middlewares: user upsert and request-scoped localisation.

Resolving the user once per update and injecting ``user``/``language``/``t``
into handler kwargs keeps handlers free of DB and i18n boilerplate, and means
the language preference is read from one place only.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.types import User as TgUser
from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import get_session_factory
from app.i18n import Translator, normalize_language
from app.models.enums import Language
from app.models.user import User

logger = get_logger(__name__)


class UserContextMiddleware(BaseMiddleware):
    """Upsert the Telegram user and inject language-aware helpers.

    The DB session is opened per update and closed immediately: aiogram
    handlers can run concurrently, and a shared session is not safe for that.
    """

    def __init__(self, session_factory: Any | None = None) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        factory = self._session_factory or get_session_factory()

        user: User | None = None
        if tg_user is not None and not tg_user.is_bot:
            async with factory() as session:
                user = await _upsert_user(session, tg_user)

        # Fall back to Telegram's client locale for a brand-new user.
        language = (
            user.language.value
            if user is not None
            else normalize_language(getattr(tg_user, "language_code", None))
        )

        data["user"] = user
        data["language"] = language
        data["t"] = Translator(language)
        return await handler(event, data)


async def _upsert_user(session: Any, tg_user: TgUser) -> User:
    """Create or refresh the local user record for a Telegram account."""
    user = await session.scalar(select(User).where(User.telegram_id == tg_user.id))

    if user is None:
        user = User(
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            # Seed from the client locale; the user can change it in /lang.
            language=Language(normalize_language(tg_user.language_code)),
        )
        session.add(user)
        logger.info("bot user created", extra={"telegram_id": tg_user.id})
    else:
        # Keep the profile fresh, but never overwrite the chosen language.
        user.username = tg_user.username
        user.first_name = tg_user.first_name
        user.last_name = tg_user.last_name

    user.last_seen_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(user)
    return user
