"""Checkout and payment status endpoints used by the TMA."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.api.deps import DbSession, Payments, Translate
from app.core.config import settings
from app.core.logging import get_logger
from app.models.order import Order, Payment
from app.models.user import User
from app.schemas import CheckoutCreate, CheckoutResponse, PaymentRead
from app.services.payments import PaymentService

logger = get_logger(__name__)
router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    payload: CheckoutCreate,
    request: Request,
    session: DbSession,
    gateway: Payments,
    t: Translate,
) -> CheckoutResponse:
    """Create a gateway payment for an order and return its payment URL."""
    order = await session.scalar(select(Order).where(Order.id == payload.order_id))
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )

    # Loaded explicitly: `order.user` is a lazy relationship and touching it
    # inside async code would raise MissingGreenlet.
    user = await session.scalar(select(User).where(User.id == order.user_id))

    service = PaymentService(session, gateway, settings)
    try:
        payment = await service.create_checkout(
            order,
            telegram_id=getattr(user, "telegram_id", None),
            username=getattr(user, "username", None),
            client_ip=request.client.host if request.client else None,
            language=t.language,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    return CheckoutResponse(
        payment_id=payment.id,
        order_id=order.id,
        status=payment.status,
        amount=float(payment.amount),
        currency=payment.currency,
        payment_url=payment.payment_url,
        message=t("order.awaiting_payment"),
    )


@router.get("/{payment_id}", response_model=PaymentRead)
async def get_payment(payment_id: str, session: DbSession, t: Translate) -> Payment:
    payment = await session.scalar(select(Payment).where(Payment.id == payment_id))
    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )
    return payment


@router.post("/{payment_id}/refresh", response_model=PaymentRead)
async def refresh_payment(
    payment_id: str, session: DbSession, gateway: Payments, t: Translate
) -> Payment:
    """Poll the gateway for a payment whose webhook never arrived."""
    payment = await session.scalar(select(Payment).where(Payment.id == payment_id))
    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )

    service = PaymentService(session, gateway, settings)
    await service.reconcile(payment)
    await session.refresh(payment)
    return payment
