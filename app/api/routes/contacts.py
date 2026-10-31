"""Registrant (WHOIS) contact profile endpoints.

The product promise is "no re-entering data": the first purchase fills a
minimal form, every later purchase reuses the saved profile. These endpoints
back that, and deliberately keep the Dynadot contact id server-side — it is an
account-level identifier and nothing the client needs.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, update

from app.api.deps import DbSession, Translate
from app.models.user import ContactProfile, User
from app.schemas import ContactCreate, ContactRead, ContactUpdate

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("", response_model=list[ContactRead])
async def list_contacts(telegram_id: int, session: DbSession) -> list[ContactProfile]:
    """Saved registrant profiles, default first."""
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        return []
    result = await session.scalars(
        select(ContactProfile)
        .where(ContactProfile.user_id == user.id)
        .order_by(ContactProfile.is_default.desc(), ContactProfile.created_at)
    )
    return list(result)


@router.post("", response_model=ContactRead, status_code=status.HTTP_201_CREATED)
async def create_contact(
    payload: ContactCreate,
    telegram_id: int,
    session: DbSession,
    t: Translate,
) -> ContactProfile:
    """Save a registrant profile for reuse on later purchases."""
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )

    data = payload.model_dump()
    make_default = data.pop("is_default", False)

    # The first profile is always the default, otherwise the customer would
    # have to pick one on a flow designed to need zero taps.
    has_any = await session.scalar(
        select(ContactProfile.id).where(ContactProfile.user_id == user.id).limit(1)
    )
    if not has_any:
        make_default = True

    if make_default:
        # Exactly one default per user: two would make contact selection
        # order-dependent and silently register under the wrong owner.
        await session.execute(
            update(ContactProfile)
            .where(ContactProfile.user_id == user.id)
            .values(is_default=False)
        )

    profile = ContactProfile(user_id=user.id, is_default=make_default, **data)
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return profile


@router.post("/{contact_id}/default", response_model=ContactRead)
async def set_default_contact(
    contact_id: str, session: DbSession, t: Translate
) -> ContactProfile:
    profile = await session.scalar(
        select(ContactProfile).where(ContactProfile.id == contact_id)
    )
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )

    await session.execute(
        update(ContactProfile)
        .where(ContactProfile.user_id == profile.user_id)
        .values(is_default=False)
    )
    profile.is_default = True
    await session.commit()
    await session.refresh(profile)
    return profile


@router.put("/{contact_id}", response_model=ContactRead)
async def update_contact(
    contact_id: str,
    payload: ContactUpdate,
    session: DbSession,
    t: Translate,
) -> ContactProfile:
    """Update an existing registrant contact profile."""
    profile = await session.scalar(
        select(ContactProfile).where(ContactProfile.id == contact_id)
    )
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )

    for field_name, value in payload.model_dump().items():
        if field_name != "is_default":
            setattr(profile, field_name, value)

    await session.commit()
    await session.refresh(profile)
    return profile
