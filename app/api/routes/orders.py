"""Order creation and status endpoints used by the TMA checkout flow.

Order creation is where the price is frozen. Reading the price again at payment
time would let a markup change (or an FX move) charge the customer something
different from what they agreed to, so the full breakdown is copied onto the
order row and never recomputed.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbSession, Dynadot, Translate
from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.dynadot.exceptions import DomainUnavailableError, DynadotError
from app.models.enums import OrderStatus
from app.models.order import Order
from app.models.user import ContactProfile, User
from app.schemas import OrderCreate, OrderRead
from app.services.pricing import PricingService, split_domain

logger = get_logger(__name__)
router = APIRouter(prefix="/orders", tags=["orders"])

#: Statuses a customer is allowed to cancel from — anything paid is the
#: refund team's business, not a self-service action.
CANCELLABLE = {OrderStatus.DRAFT, OrderStatus.AWAITING_PAYMENT}


@router.post("", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
async def create_order(
    payload: OrderCreate,
    telegram_id: int,
    session: DbSession,
    dynadot: Dynadot,
    t: Translate,
) -> Order:
    """Create a DRAFT order with its price frozen at today's quote."""
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )
    if user.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=t("error.forbidden")
        )

    domain_name = payload.domain_name.strip().lower().lstrip(".")
    base, tld = split_domain(domain_name)
    if not base or not tld:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=t("search.invalid_name"),
        )

    # Reusing an open draft keeps a customer who taps "buy" twice on one order
    # instead of creating a second payable row for the same name.
    existing = await session.scalar(
        select(Order).where(
            Order.user_id == user.id,
            Order.domain_name == domain_name,
            Order.status.in_(list(CANCELLABLE)),
        )
    )
    if existing is not None:
        return existing

    try:
        search = await dynadot.ensure_available(domain_name)
    except DomainUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=t("search.taken")
        ) from exc
    except DynadotError as exc:
        logger.warning("availability check failed", extra={"error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=t("error.registrar_unavailable"),
        ) from exc

    pricing = PricingService(session, settings)
    quote = await pricing.quote(
        tld=tld, cost_usd=_price_from_search(search), years=payload.years
    )
    if quote.price_rub <= 0:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=t("error.registrar_unavailable"),
        )

    contact_profile_id = payload.contact_profile_id or await _default_contact_id(
        session, user.id
    )

    order = Order(
        user_id=user.id,
        contact_profile_id=contact_profile_id,
        domain_name=domain_name,
        tld=tld,
        years=payload.years,
        status=OrderStatus.DRAFT,
        cost_usd=quote.cost_usd,
        price_rub=quote.price_rub,
        markup_percent=quote.markup_percent,
        fx_rate=quote.fx_rate,
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)

    logger.info(
        "order created",
        extra={
            "order_id": order.id,
            "domain": order.domain_name,
            "price_rub": quote.price_rub,
        },
    )
    return order


@router.get("", response_model=list[OrderRead])
async def list_orders(
    telegram_id: int,
    session: DbSession,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[Order]:
    """The customer's orders, newest first."""
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        return []
    result = await session.scalars(
        select(Order)
        .where(Order.user_id == user.id)
        .order_by(Order.created_at.desc())
        .limit(limit)
    )
    return list(result)


@router.get("/{order_id}", response_model=OrderRead)
async def get_order(order_id: str, session: DbSession, t: Translate) -> Order:
    order = await session.scalar(select(Order).where(Order.id == order_id))
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )
    return order


@router.post("/{order_id}/cancel", response_model=OrderRead)
async def cancel_order(order_id: str, session: DbSession, t: Translate) -> Order:
    """Abandon an unpaid order."""
    order = await session.scalar(select(Order).where(Order.id == order_id))
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )
    if order.status not in CANCELLABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"order is not cancellable in status {order.status.value}",
        )
    order.status = OrderStatus.CANCELLED
    await session.commit()
    await session.refresh(order)
    return order


async def _default_contact_id(session: AsyncSession, user_id: str) -> str | None:
    """Pick the saved registrant profile so repeat buyers type nothing."""
    profile = await session.scalar(
        select(ContactProfile)
        .where(ContactProfile.user_id == user_id)
        .order_by(ContactProfile.is_default.desc(), ContactProfile.created_at)
        .limit(1)
    )
    return profile.id if profile else None


def _price_from_search(search: dict) -> float:
    """Pull the registration cost out of a search response.

    The v2 API nests prices differently per endpoint and is still Beta, so
    several shapes are tolerated; 0 falls back to the stored TLD cost.
    """
    for key in ("register_price", "registration_price", "price", "register"):
        value = search.get(key)
        if isinstance(value, int | float) and value > 0:
            return float(value)
        if isinstance(value, str):
            cleaned = value.replace("$", "").replace(",", "").strip()
            try:
                parsed = float(cleaned)
            except ValueError:
                continue
            if parsed > 0:
                return parsed
        if isinstance(value, dict):
            nested = _price_from_search(value)
            if nested > 0:
                return nested
    price_list = search.get("price_list")
    if isinstance(price_list, list):
        for entry in price_list:
            if isinstance(entry, dict):
                nested = _price_from_search(entry)
                if nested > 0:
                    return nested
    return 0.0
