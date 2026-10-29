"""User profile and language-preference endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DbSession, Translate
from app.models import User
from app.schemas import LanguageUpdate, UserRead

router = APIRouter(prefix="/users", tags=["users"])


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
