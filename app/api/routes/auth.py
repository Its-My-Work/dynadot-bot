"""Authentication and account linking routes (Email & Telegram)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import delete, func, select, update

from app.api.deps import DbSession
from app.bot.notifications import safe_send
from app.bot.runner import create_bot
from app.core.logging import get_logger
from app.models.domain import Domain
from app.models.order import Order
from app.models.user import ContactProfile, TelegramLoginCode, User
from app.models.vps import ServerInstance
from app.schemas import (
    AuthResponse,
    EmailLoginRequest,
    EmailRegisterRequest,
    LinkEmailRequest,
    TelegramCodeRequest,
    TelegramCodeVerify,
    TelegramLinkResponse,
    UserRead,
)
from app.services.security import (
    generate_auth_token,
    generate_login_code,
    hash_code,
    hash_password,
    verify_password,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED
)
async def register_email(
    payload: EmailRegisterRequest,
    session: DbSession,
) -> AuthResponse:
    """Register an account via Email & Password, or bind them to an existing Telegram session."""
    norm_email = payload.email.strip().lower()

    # Check if email is already registered
    existing_user = await session.scalar(
        select(User).where(func.lower(User.email) == norm_email)
    )
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Этот адрес электронной почты уже зарегистрирован. Пожалуйста, выполните вход.",
        )

    # If telegram_id was supplied (e.g. from current web session or TMA), try to attach to it
    if payload.telegram_id:
        user = await session.scalar(
            select(User).where(User.telegram_id == payload.telegram_id)
        )
        if user is not None:
            user.email = norm_email
            user.password_hash = hash_password(payload.password)
            user.has_password = True
            user.last_seen_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(user)
            token = generate_auth_token(user.id)
            return AuthResponse(
                user=UserRead.model_validate(user),
                token=token,
                message="Email и пароль успешно привязаны к вашему аккаунту!",
            )

    # Brand new standalone email user
    # Allocate a unique fallback identifier in the 9_xxx_xxx_xxx space for foreign keys
    fallback_tid = 9_000_000_000 + secrets.randbelow(900_000_000)
    user = User(
        email=norm_email,
        password_hash=hash_password(payload.password),
        has_password=True,
        telegram_id=fallback_tid,
        username=norm_email.split("@")[0][:32],
        last_seen_at=datetime.now(UTC),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    token = generate_auth_token(user.id)
    return AuthResponse(
        user=UserRead.model_validate(user),
        token=token,
        message="Регистрация успешно завершена!",
    )


@router.post("/login", response_model=AuthResponse)
async def login_email(
    payload: EmailLoginRequest,
    session: DbSession,
) -> AuthResponse:
    """Authenticate with Email and Password."""
    norm_email = payload.email.strip().lower()

    user = await session.scalar(select(User).where(func.lower(User.email) == norm_email))
    if (
        user is None
        or not user.has_password
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный адрес почты или пароль.",
        )

    user.last_seen_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(user)

    token = generate_auth_token(user.id)
    return AuthResponse(
        user=UserRead.model_validate(user),
        token=token,
        message="Вход выполнен успешно!",
    )


@router.post("/telegram-code/request")
async def request_telegram_code(
    payload: TelegramCodeRequest,
    session: DbSession,
) -> dict[str, str | bool]:
    """Send a single-use 6-digit login code to user's Telegram."""
    norm_username = payload.username.strip().lstrip("@").lower()

    # Search by telegram_username or standard username
    user = await session.scalar(
        select(User).where(
            (
                (func.lower(User.telegram_username) == norm_username)
                | (func.lower(User.username) == norm_username)
            )
            & (User.telegram_id.isnot(None))
            & (User.telegram_id < 9_000_000_000)
        )
    )
    if user is None or not user.telegram_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Пользователь @{norm_username} не найден или Telegram ещё не привязан к аккаунту. Сначала запустите нашего бота @itsmywork_ai_bot.",
        )

    # Invalidate old codes
    await session.execute(
        delete(TelegramLoginCode).where(TelegramLoginCode.telegram_id == user.telegram_id)
    )

    code = generate_login_code()
    login_entry = TelegramLoginCode(
        telegram_id=user.telegram_id,
        code_hash=hash_code(code),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    session.add(login_entry)
    await session.commit()

    # Dispatch to Telegram bot
    try:
        bot = create_bot()
        text = (
            f"🔑 <b>Код авторизации на dynadot.itsmywork.vip</b>\n\n"
            f"Ваш одноразовый проверочный код: <code>{code}</code>\n\n"
            f"⏳ Код действует 5 минут. Если вы не запрашивали вход, проигнорируйте это сообщение."
        )
        await safe_send(bot, user.telegram_id, text)
        await bot.session.close()
    except Exception as exc:
        logger.warning("failed to send login code via bot", extra={"error": str(exc)})

    return {
        "ok": True,
        "message": f"Код подтверждения отправлен в личные сообщения Telegram пользователю @{norm_username}",
    }


@router.post("/telegram-code/verify", response_model=AuthResponse)
async def verify_telegram_code(
    payload: TelegramCodeVerify,
    session: DbSession,
) -> AuthResponse:
    """Verify single-use 6-digit code sent to Telegram."""
    norm_username = payload.username.strip().lstrip("@").lower()

    user = await session.scalar(
        select(User).where(
            (
                (func.lower(User.telegram_username) == norm_username)
                | (func.lower(User.username) == norm_username)
            )
            & (User.telegram_id.isnot(None))
            & (User.telegram_id < 9_000_000_000)
        )
    )
    if user is None or not user.telegram_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Пользователь @{norm_username} не найден.",
        )

    record = await session.scalar(
        select(TelegramLoginCode)
        .where(
            (TelegramLoginCode.telegram_id == user.telegram_id)
            & (TelegramLoginCode.expires_at > datetime.now(UTC))
        )
        .order_by(TelegramLoginCode.created_at.desc())
    )

    if record is None or record.code_hash != hash_code(payload.code):
        if record is not None:
            record.attempts += 1
            await session.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Неверный или просроченный код подтверждения.",
        )

    # Valid! Clean up used code
    await session.delete(record)
    user.last_seen_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(user)

    token = generate_auth_token(user.id)
    return AuthResponse(
        user=UserRead.model_validate(user),
        token=token,
        message="Вход через Telegram успешно выполнен!",
    )


@router.post("/link-telegram/generate", response_model=TelegramLinkResponse)
async def generate_telegram_link(
    session: DbSession,
    telegram_id: int = Query(...),
) -> TelegramLinkResponse:
    """Generate a 1-time deep link to the Telegram bot to link TG account to this profile."""
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пользователь не найден.",
        )

    link_token = secrets.token_urlsafe(16)
    user.telegram_link_code = link_token
    user.telegram_link_expires = datetime.now(UTC) + timedelta(minutes=15)
    await session.commit()

    bot_url = f"https://t.me/itsmywork_ai_bot?start=link_{link_token}"
    return TelegramLinkResponse(bot_url=bot_url, expires_in_seconds=900)


@router.post("/link-email", response_model=AuthResponse)
async def link_email(
    payload: LinkEmailRequest,
    session: DbSession,
) -> AuthResponse:
    """Link an Email & Password to the current user profile (with automatic account merging)."""
    norm_email = payload.email.strip().lower()

    user = await session.scalar(
        select(User).where(User.telegram_id == payload.telegram_id)
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Текущий пользователь не найден.",
        )

    # Check if another user already has this email
    existing = await session.scalar(
        select(User).where(func.lower(User.email) == norm_email)
    )
    if existing is not None and existing.id != user.id:
        # Merge existing into current user!
        await session.execute(
            update(ContactProfile)
            .where(ContactProfile.user_id == existing.id)
            .values(user_id=user.id)
        )
        await session.execute(
            update(Order).where(Order.user_id == existing.id).values(user_id=user.id)
        )
        await session.execute(
            update(Domain).where(Domain.user_id == existing.id).values(user_id=user.id)
        )
        await session.execute(
            update(ServerInstance)
            .where(ServerInstance.user_id == existing.id)
            .values(user_id=user.id)
        )
        await session.delete(existing)

    user.email = norm_email
    user.password_hash = hash_password(payload.password)
    user.has_password = True
    user.last_seen_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(user)

    token = generate_auth_token(user.id)
    return AuthResponse(
        user=UserRead.model_validate(user),
        token=token,
        message="Email и пароль успешно привязаны к аккаунту!",
    )


@router.get("/me", response_model=UserRead)
async def get_current_user(
    session: DbSession,
    telegram_id: int = Query(...),
) -> UserRead:
    """Fetch current user profile state."""
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пользователь не найден.",
        )
    return UserRead.model_validate(user)
