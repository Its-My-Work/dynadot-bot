"""Payment orchestration: checkout creation and webhook application.

This is the only place that moves `Order.status` in response to money
movement. Two invariants drive the design:

1. **Idempotency.** Gateways redeliver callbacks (Platega resends CONFIRMED
   after a dispute clears), so applying the same event twice must be a no-op.
2. **Never silently keep money.** A domain can be registered by someone else
   between taking payment and calling Dynadot, so a paid order whose domain is
   gone becomes `REFUND_REQUIRED`, not `FAILED`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.integrations.payments.base import (
    PaymentGateway,
    PaymentIntent,
    WebhookEvent,
)
from app.integrations.payments.exceptions import PaymentNotFoundError
from app.models.enums import Currency, OrderStatus, PaymentStatus
from app.models.order import Order, Payment

logger = get_logger(__name__)

#: Order states from which a fresh checkout may be started.
CHECKOUT_ALLOWED_FROM = {OrderStatus.DRAFT, OrderStatus.AWAITING_PAYMENT}

#: Payment states that are final — a later event must not overwrite them,
#: except for the explicit transitions listed in ``_ALLOWED_OVERRIDES``.
TERMINAL_PAYMENT_STATES = {
    PaymentStatus.SUCCEEDED,
    PaymentStatus.REFUNDED,
}

#: Legitimate transitions out of an already-terminal payment state.
#: CONFIRMED after CANCELED is real on Platega: a cancelled-looking payment can
#: still settle, and the customer has paid, so it must be credited.
_ALLOWED_OVERRIDES: dict[PaymentStatus, set[PaymentStatus]] = {
    PaymentStatus.SUCCEEDED: {PaymentStatus.REFUNDED},
    PaymentStatus.FAILED: {PaymentStatus.SUCCEEDED, PaymentStatus.REFUNDED},
    PaymentStatus.EXPIRED: {PaymentStatus.SUCCEEDED},
    PaymentStatus.PENDING: {
        PaymentStatus.SUCCEEDED,
        PaymentStatus.FAILED,
        PaymentStatus.EXPIRED,
        PaymentStatus.REFUNDED,
    },
}


class PaymentApplied:
    """Outcome of applying a webhook event, for logging and tests."""

    __slots__ = ("changed", "order", "payment", "reason")

    def __init__(
        self,
        *,
        payment: Payment,
        order: Order | None,
        changed: bool,
        reason: str,
    ) -> None:
        self.payment = payment
        self.order = order
        self.changed = changed
        self.reason = reason

    def __repr__(self) -> str:
        return f"<PaymentApplied changed={self.changed} reason={self.reason}>"


class PaymentService:
    """Creates checkouts and applies gateway callbacks to our own state."""

    def __init__(
        self,
        session: AsyncSession,
        gateway: PaymentGateway,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._gateway = gateway
        self._settings = settings or get_settings()

    # --------------------------------------------------------------- checkout

    async def create_checkout(
        self,
        order: Order,
        *,
        telegram_id: int | None = None,
        username: str | None = None,
        client_ip: str | None = None,
        language: str = "ru",
    ) -> Payment:
        """Create (or reuse) a pending payment for ``order``.

        Reusing an existing pending payment keeps the customer on one
        transaction instead of littering the gateway with abandoned ones.
        """
        if order.status not in CHECKOUT_ALLOWED_FROM:
            raise ValueError(
                f"order {order.id} is not payable in status {order.status.value}"
            )

        existing = await self._find_pending_payment(order.id)
        if existing is not None and existing.payment_url:
            logger.info(
                "reusing pending payment",
                extra={"order_id": order.id, "payment_id": existing.id},
            )
            return existing

        amount = float(order.price_rub or 0)
        if amount <= 0:
            raise ValueError(f"order {order.id} has no payable amount")

        intent = PaymentIntent(
            order_id=order.id,
            amount=amount,
            currency=Currency.RUB,
            description=_describe(order, language),
            user_id=str(telegram_id) if telegram_id else None,
            user_name=f"@{username}" if username else None,
            client_ip=client_ip,
            method=self._settings.platega_payment_method,
        )

        session_data = await self._gateway.create_payment(intent)

        payment = Payment(
            order_id=order.id,
            provider=self._gateway.provider,
            status=session_data.status,
            amount=Decimal(str(amount)),
            currency=Currency.RUB,
            external_id=session_data.external_id,
            payment_url=session_data.payment_url,
        )
        self._session.add(payment)

        order.status = OrderStatus.AWAITING_PAYMENT
        await self._session.commit()
        await self._session.refresh(payment)

        logger.info(
            "checkout created",
            extra={
                "order_id": order.id,
                "payment_id": payment.id,
                "external_id": payment.external_id,
            },
        )
        return payment

    async def _find_pending_payment(self, order_id: str) -> Payment | None:
        return await self._session.scalar(
            select(Payment)
            .where(
                Payment.order_id == order_id,
                Payment.status == PaymentStatus.PENDING,
                Payment.provider == self._gateway.provider,
            )
            .order_by(Payment.created_at.desc())
            .limit(1)
        )

    # ---------------------------------------------------------------- webhook

    async def apply_webhook(self, event: WebhookEvent) -> PaymentApplied:
        """Apply a normalised callback idempotently.

        Lookup is by gateway transaction id first (the true idempotency key),
        falling back to the echoed order id so a payment created out-of-band
        can still be matched.
        """
        payment = await self._session.scalar(
            select(Payment).where(Payment.external_id == event.external_id)
        )

        if payment is None and event.order_id:
            payment = await self._session.scalar(
                select(Payment)
                .where(Payment.order_id == event.order_id)
                .order_by(Payment.created_at.desc())
                .limit(1)
            )
            if payment is not None and not payment.external_id:
                payment.external_id = event.external_id

        if payment is None:
            raise PaymentNotFoundError(
                f"no payment matches transaction {event.external_id}",
                payload=event.raw,
            )

        # Always keep the newest raw body for dispute resolution.
        payment.raw_payload = _dump(event.raw)

        if payment.status == event.status:
            await self._session.commit()
            logger.info(
                "webhook ignored (already applied)",
                extra={
                    "payment_id": payment.id,
                    "status": payment.status.value,
                },
            )
            return PaymentApplied(
                payment=payment,
                order=await self._load_order(payment.order_id),
                changed=False,
                reason="duplicate",
            )

        allowed = _ALLOWED_OVERRIDES.get(payment.status, set())
        if payment.status in TERMINAL_PAYMENT_STATES and event.status not in allowed:
            await self._session.commit()
            logger.warning(
                "webhook rejected (illegal transition)",
                extra={
                    "payment_id": payment.id,
                    "from": payment.status.value,
                    "to": event.status.value,
                },
            )
            return PaymentApplied(
                payment=payment,
                order=await self._load_order(payment.order_id),
                changed=False,
                reason="illegal_transition",
            )

        previous = payment.status
        payment.status = event.status
        order = await self._load_order(payment.order_id)

        if event.status is PaymentStatus.SUCCEEDED:
            payment.paid_at = payment.paid_at or datetime.now(UTC)
            # CANCELLED is included deliberately: a cancelled-looking payment
            # can still settle later, and once the money has arrived the order
            # must be revived rather than left cancelled with funds taken.
            if order is not None and order.status in {
                OrderStatus.DRAFT,
                OrderStatus.AWAITING_PAYMENT,
                OrderStatus.FAILED,
                OrderStatus.CANCELLED,
            }:
                # PAID — not REGISTERING: the registration worker claims it,
                # re-checking availability before spending the registrar call.
                order.status = OrderStatus.PAID
        elif event.status is PaymentStatus.REFUNDED:
            payment.refunded_at = payment.refunded_at or datetime.now(UTC)
            if order is not None:
                order.status = OrderStatus.REFUNDED
        elif event.status in {PaymentStatus.FAILED, PaymentStatus.EXPIRED}:
            # Only the unpaid states move the order; a failure arriving after
            # the domain is already registered must not cancel it.
            if order is not None and order.status in {
                OrderStatus.DRAFT,
                OrderStatus.AWAITING_PAYMENT,
            }:
                order.status = OrderStatus.CANCELLED

        await self._session.commit()
        await self._session.refresh(payment)

        logger.info(
            "webhook applied",
            extra={
                "payment_id": payment.id,
                "from": previous.value,
                "to": payment.status.value,
                "order_status": order.status.value if order else None,
            },
        )
        return PaymentApplied(
            payment=payment, order=order, changed=True, reason="applied"
        )

    # ------------------------------------------------------------ reconcile

    async def reconcile(self, payment: Payment) -> PaymentApplied | None:
        """Poll the gateway for a stale pending payment.

        Needed because a dropped webhook would otherwise leave a paying
        customer stuck in AWAITING_PAYMENT forever.
        """
        if not payment.external_id:
            return None
        session_data = await self._gateway.get_status(payment.external_id)
        event = WebhookEvent(
            provider=self._gateway.provider,
            external_id=payment.external_id,
            status=session_data.status,
            amount=session_data.amount,
            currency=session_data.currency,
            order_id=payment.order_id,
            raw=session_data.raw,
        )
        return await self.apply_webhook(event)

    async def _load_order(self, order_id: str) -> Order | None:
        return await self._session.scalar(select(Order).where(Order.id == order_id))


def _describe(order: Order, language: str) -> str:
    from app.i18n import translate

    key = "payment.description"
    text = translate(key, language, domain=order.domain_name, years=order.years)
    return text if text != key else f"Domain {order.domain_name} ({order.years}y)"


def _dump(payload: dict) -> str:
    import json

    try:
        return json.dumps(payload, ensure_ascii=False)[:4000]
    except (TypeError, ValueError):
        return str(payload)[:4000]
