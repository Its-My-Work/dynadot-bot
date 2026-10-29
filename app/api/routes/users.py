"""User profile and language-preference endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DbSession, Translate
from app.models import User
from app.schemas import LanguageUpdate, ThemeUpdate, UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register_user(telegram_id: int, session: DbSession) -> User:
    """Ensure a user record exists in PostgreSQL for web or first-time TMA users."""
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(telegram_id=telegram_id, username=f"user_{telegram_id}")
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


@router.get("/{telegram_id}", response_model=UserRead)
async def get_user(telegram_id: int, session: DbSession, t: Translate) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )
    return user


@router.patch("/{telegram_id}/language", response_model=UserRead)
async def set_language(
    telegram_id: int,
    payload: LanguageUpdate,
    session: DbSession,
    t: Translate,
) -> User:
    """Persist the language preference so it survives across bot and TMA."""
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )

    user.language = payload.language
    await session.commit()
    await session.refresh(user)
    return user


@router.patch("/{telegram_id}/theme", response_model=UserRead)
async def set_theme(
    telegram_id: int,
    payload: ThemeUpdate,
    session: DbSession,
    t: Translate,
) -> User:
    """Persist the design theme so the choice follows the account.

    The client applies the theme locally *before* calling this, and keeps its
    own localStorage copy: a failed PATCH must never leave the user staring at
    a palette they just switched away from.
    """
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )

    user.theme = payload.theme
    await session.commit()
    await session.refresh(user)
    return user
