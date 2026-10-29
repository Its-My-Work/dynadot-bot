"""Tests for PaymentService: checkout creation and idempotent webhooks."""

from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.integrations.payments.base import (
    PaymentGateway,
    PaymentIntent,
    PaymentSession,
    WebhookEvent,
)
from app.integrations.payments.exceptions import PaymentNotFoundError
from app.models import Base
from app.models.enums import (
    Currency,
    OrderStatus,
    PaymentProvider,
    PaymentStatus,
)
from app.models.order import Order, Payment
from app.models.user import User
from app.services.payments import PaymentService


class FakeGateway(PaymentGateway):
    """In-memory gateway so service logic is tested without HTTP."""

    provider = PaymentProvider.PLATEGA

    def __init__(self, *, status: PaymentStatus = PaymentStatus.PENDING) -> None:
        self.status = status
        self.created: list[PaymentIntent] = []
        self.counter = 0
        self.status_response: PaymentSession | None = None

    async def create_payment(self, intent: PaymentIntent) -> PaymentSession:
        self.created.append(intent)
        self.counter += 1
        return PaymentSession(
            external_id=f"tx-{self.counter}",
            payment_url=f"https://pay.test/{self.counter}",
            status=self.status,
            amount=intent.amount,
            currency=intent.currency,
        )

    async def get_status(self, external_id: str) -> PaymentSession:
        if self.status_response is not None:
            return self.status_response
        return PaymentSession(
            external_id=external_id, payment_url=None, status=self.status
        )

    def verify_webhook(self, headers, body) -> None:  # pragma: no cover
        return None

    def parse_webhook(self, payload):  # pragma: no cover
        raise NotImplementedError


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    """A real SQLite database so commits and constraints are exercised."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/svc.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def db(session_factory):
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def order(db) -> Order:
    user = User(telegram_id=424242, username="buyer")
    db.add(user)
    await db.commit()
    await db.refresh(user)

    order = Order(
        user_id=user.id,
        domain_name="example.com",
        tld="com",
        years=1,
        status=OrderStatus.DRAFT,
        cost_usd=Decimal("10.00"),
        price_rub=Decimal("1187.50"),
        markup_percent=Decimal("25.00"),
        fx_rate=Decimal("95.0000"),
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return order


def make_service(db, gateway) -> PaymentService:
    return PaymentService(
        db, gateway, Settings(platega_merchant_id="m", platega_secret="s")
    )


# ------------------------------------------------------------------ checkout


@pytest.mark.asyncio
async def test_create_checkout_persists_payment_and_awaits_payment(db, order):
    gateway = FakeGateway()
    payment = await make_service(db, gateway).create_checkout(
        order, telegram_id=424242, username="buyer"
    )

    assert payment.external_id == "tx-1"
    assert payment.payment_url == "https://pay.test/1"
    assert payment.status == PaymentStatus.PENDING
    assert float(payment.amount) == 1187.50
    assert payment.currency == Currency.RUB
    # The order must now be awaiting payment, not still a draft.
    assert order.status == OrderStatus.AWAITING_PAYMENT

    intent = gateway.created[0]
    assert intent.order_id == order.id
    assert intent.user_id == "424242"
    assert intent.user_name == "@buyer"


@pytest.mark.asyncio
async def test_create_checkout_reuses_existing_pending_payment(db, order):
    """A second checkout must not litter the gateway with abandoned txs."""
    gateway = FakeGateway()
    service = make_service(db, gateway)

    first = await service.create_checkout(order)
    second = await service.create_checkout(order)

    assert first.id == second.id
    assert len(gateway.created) == 1


@pytest.mark.asyncio
async def test_create_checkout_rejects_an_already_paid_order(db, order):
    order.status = OrderStatus.PAID
    await db.commit()

    with pytest.raises(ValueError, match="not payable"):
        await make_service(db, FakeGateway()).create_checkout(order)


@pytest.mark.asyncio
async def test_create_checkout_rejects_a_zero_amount_order(db, order):
    order.price_rub = Decimal("0")
    await db.commit()

    with pytest.raises(ValueError, match="no payable amount"):
        await make_service(db, FakeGateway()).create_checkout(order)


# ------------------------------------------------------------------- webhook


async def _checkout(db, gateway, order) -> Payment:
    return await make_service(db, gateway).create_checkout(order)


def confirmed(payment: Payment, order: Order) -> WebhookEvent:
    return WebhookEvent(
        provider=PaymentProvider.PLATEGA,
        external_id=payment.external_id,
        status=PaymentStatus.SUCCEEDED,
        amount=float(payment.amount),
        currency=Currency.RUB,
        order_id=order.id,
        raw_status="CONFIRMED",
        raw={"id": payment.external_id, "status": "CONFIRMED"},
    )


@pytest.mark.asyncio
async def test_confirmed_webhook_marks_order_paid(db, order):
    gateway = FakeGateway()
    service = make_service(db, gateway)
    payment = await service.create_checkout(order)

    result = await service.apply_webhook(confirmed(payment, order))

    assert result.changed is True
    assert result.payment.status == PaymentStatus.SUCCEEDED
    assert result.payment.paid_at is not None
    # PAID, not REGISTERING: the worker claims it after re-checking the domain.
    assert result.order.status == OrderStatus.PAID
    # The raw body is kept for dispute resolution.
    assert "CONFIRMED" in result.payment.raw_payload


@pytest.mark.asyncio
async def test_duplicate_confirmed_webhook_is_a_no_op(db, order):
    """Platega resends CONFIRMED after a dispute clears; it must not double-apply."""
    gateway = FakeGateway()
    service = make_service(db, gateway)
    payment = await service.create_checkout(order)

    first = await service.apply_webhook(confirmed(payment, order))
    paid_at = first.payment.paid_at

    second = await service.apply_webhook(confirmed(payment, order))

    assert second.changed is False
    assert second.reason == "duplicate"
    assert second.payment.paid_at == paid_at
    assert second.order.status == OrderStatus.PAID


@pytest.mark.asyncio
async def test_confirmed_after_canceled_still_credits_the_customer(db, order):
    """A cancelled-looking payment can still settle — the customer paid."""
    gateway = FakeGateway()
    service = make_service(db, gateway)
    payment = await service.create_checkout(order)

    await service.apply_webhook(
        WebhookEvent(
            provider=PaymentProvider.PLATEGA,
            external_id=payment.external_id,
            status=PaymentStatus.FAILED,
            order_id=order.id,
            raw_status="CANCELED",
        )
    )
    assert payment.status == PaymentStatus.FAILED

    result = await service.apply_webhook(confirmed(payment, order))

    assert result.changed is True
    assert result.payment.status == PaymentStatus.SUCCEEDED
    assert result.order.status == OrderStatus.PAID


@pytest.mark.asyncio
async def test_failed_webhook_after_success_is_rejected(db, order):
    """A late failure must never cancel an order that was already paid."""
    gateway = FakeGateway()
    service = make_service(db, gateway)
    payment = await service.create_checkout(order)
    await service.apply_webhook(confirmed(payment, order))

    result = await service.apply_webhook(
        WebhookEvent(
            provider=PaymentProvider.PLATEGA,
            external_id=payment.external_id,
            status=PaymentStatus.FAILED,
            order_id=order.id,
            raw_status="CANCELED",
        )
    )

    assert result.changed is False
    assert result.reason == "illegal_transition"
    assert result.payment.status == PaymentStatus.SUCCEEDED
    assert result.order.status == OrderStatus.PAID


@pytest.mark.asyncio
async def test_canceled_webhook_cancels_an_unpaid_order(db, order):
    gateway = FakeGateway()
    service = make_service(db, gateway)
    payment = await service.create_checkout(order)

    result = await service.apply_webhook(
        WebhookEvent(
            provider=PaymentProvider.PLATEGA,
            external_id=payment.external_id,
            status=PaymentStatus.FAILED,
            order_id=order.id,
            raw_status="CANCELED",
        )
    )

    assert result.changed is True
    assert result.order.status == OrderStatus.CANCELLED


@pytest.mark.asyncio
async def test_expired_webhook_cancels_an_unpaid_order(db, order):
    gateway = FakeGateway()
    service = make_service(db, gateway)
    payment = await service.create_checkout(order)

    result = await service.apply_webhook(
        WebhookEvent(
            provider=PaymentProvider.PLATEGA,
            external_id=payment.external_id,
            status=PaymentStatus.EXPIRED,
            order_id=order.id,
            raw_status="EXPIRED",
        )
    )

    assert result.payment.status == PaymentStatus.EXPIRED
    assert result.order.status == OrderStatus.CANCELLED


@pytest.mark.asyncio
async def test_refund_webhook_marks_order_refunded(db, order):
    gateway = FakeGateway()
    service = make_service(db, gateway)
    payment = await service.create_checkout(order)
    await service.apply_webhook(confirmed(payment, order))

    result = await service.apply_webhook(
        WebhookEvent(
            provider=PaymentProvider.PLATEGA,
            external_id=payment.external_id,
            status=PaymentStatus.REFUNDED,
            order_id=order.id,
            raw_status="REFUNDED",
        )
    )

    assert result.changed is True
    assert result.payment.refunded_at is not None
    assert result.order.status == OrderStatus.REFUNDED


@pytest.mark.asyncio
async def test_webhook_for_unknown_transaction_raises_not_found(db):
    service = make_service(db, FakeGateway())
    with pytest.raises(PaymentNotFoundError):
        await service.apply_webhook(
            WebhookEvent(
                provider=PaymentProvider.PLATEGA,
                external_id="tx-does-not-exist",
                status=PaymentStatus.SUCCEEDED,
            )
        )


@pytest.mark.asyncio
async def test_webhook_falls_back_to_order_id_and_backfills_external_id(db, order):
    """A payment created without an external id can still be matched."""
    payment = Payment(
        order_id=order.id,
        provider=PaymentProvider.PLATEGA,
        status=PaymentStatus.PENDING,
        amount=Decimal("1187.50"),
        currency=Currency.RUB,
        external_id=None,
    )
    db.add(payment)
    order.status = OrderStatus.AWAITING_PAYMENT
    await db.commit()

    service = make_service(db, FakeGateway())
    result = await service.apply_webhook(
        WebhookEvent(
            provider=PaymentProvider.PLATEGA,
            external_id="tx-late",
            status=PaymentStatus.SUCCEEDED,
            order_id=order.id,
            raw_status="CONFIRMED",
        )
    )

    assert result.changed is True
    assert result.payment.external_id == "tx-late"
    assert result.order.status == OrderStatus.PAID


# ----------------------------------------------------------------- reconcile


@pytest.mark.asyncio
async def test_reconcile_applies_the_polled_status(db, order):
    """Covers a dropped webhook leaving the customer stuck in AWAITING_PAYMENT."""
    gateway = FakeGateway()
    service = make_service(db, gateway)
    payment = await service.create_checkout(order)

    gateway.status_response = PaymentSession(
        external_id=payment.external_id,
        payment_url=None,
        status=PaymentStatus.SUCCEEDED,
        amount=float(payment.amount),
        currency=Currency.RUB,
    )

    result = await service.reconcile(payment)

    assert result is not None
    assert result.changed is True
    assert result.payment.status == PaymentStatus.SUCCEEDED
    assert result.order.status == OrderStatus.PAID


@pytest.mark.asyncio
async def test_reconcile_without_external_id_does_nothing(db, order):
    payment = Payment(
        order_id=order.id,
        provider=PaymentProvider.PLATEGA,
        status=PaymentStatus.PENDING,
        amount=Decimal("100"),
        currency=Currency.RUB,
        external_id=None,
    )
    db.add(payment)
    await db.commit()

    assert await make_service(db, FakeGateway()).reconcile(payment) is None
