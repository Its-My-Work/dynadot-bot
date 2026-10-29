"""Admin endpoints: TLD markup management and Dynadot API log inspection."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import DbSession, Dynadot, Translate
from app.models import DynadotApiLog, TldPricing
from app.schemas import ApiLogRead, TldPricingRead, TldPricingUpdate

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/tld-pricing", response_model=list[TldPricingRead])
async def list_tld_pricing(session: DbSession) -> list[TldPricing]:
    result = await session.scalars(
        select(TldPricing).order_by(TldPricing.sort_order, TldPricing.tld)
    )
    return list(result)


@router.patch("/tld-pricing/{tld}", response_model=TldPricingRead)
async def update_tld_pricing(
    tld: str,
    payload: TldPricingUpdate,
    session: DbSession,
    t: Translate,
) -> TldPricing:
    """Adjust the resale markup for one zone."""
    pricing = await session.scalar(
        select(TldPricing).where(TldPricing.tld == tld.lstrip("."))
    )
    if pricing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(pricing, field, value)

    await session.commit()
    await session.refresh(pricing)
    return pricing


@router.get("/api-logs", response_model=list[ApiLogRead])
async def list_api_logs(
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[DynadotApiLog]:
    """Most recent Dynadot calls, newest first."""
    result = await session.scalars(
        select(DynadotApiLog)
        .order_by(DynadotApiLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result)


@router.get("/queue-status")
async def queue_status(dynadot: Dynadot) -> dict[str, int | bool]:
    """Depth of the serialising Dynadot queue — key operational signal."""
    return {"pending": dynadot.queue.pending}
