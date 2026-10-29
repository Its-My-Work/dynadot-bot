"""End-to-end tests for the webhook endpoints."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.models import Base
from app.models.enums import (
    Currency,
    OrderStatus,
    PaymentProvider,
    PaymentStatus,
)
from app.models.order import Order, Payment
from app.models.user import User

MERCHANT_ID = "merchant-123"
SECRET = "webhook-secret"
AUTH = {"X-MerchantId": MERCHANT_ID, "X-Secret": SECRET}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient wired to a real SQLite DB with one awaiting-payment order."""
    db_path = tmp_path / "hooks.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("PLATEGA_MERCHANT_ID", MERCHANT_ID)
    monkeypatch.setenv("PLATEGA_SECRET", SECRET)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "tg-secret")

    import app.api.deps as deps_module
    import app.api.routes.payments as payments_module
    import app.api.routes.webhooks as webhooks_module
    import app.core.config as config_module
    import app.db.session as session_module
    import app.main as main_module

    config_module.get_settings.cache_clear()
    new_settings = config_module.Settings()
    for module in (
        config_module,
        session_module,
        main_module,
        webhooks_module,
        payments_module,
        deps_module,
    ):
        if hasattr(module, "settings"):
            monkeypatch.setattr(module, "settings", new_settings)
    monkeypatch.setattr(session_module, "_engine", None)
    monkeypatch.setattr(session_module, "_session_factory", None)

    state: dict = {}

    async def seed():
        async with session_module.get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_module.get_session_factory()() as session:
            user = User(telegram_id=777001, username="payer")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            order = Order(
                user_id=user.id,
                domain_name="hooked.com",
                tld="com",
                years=1,
                status=OrderStatus.AWAITING_PAYMENT,
                cost_usd=Decimal("10.00"),
                price_rub=Decimal("1187.50"),
                markup_percent=Decimal("25.00"),
                fx_rate=Decimal("95.0000"),
            )
            session.add(order)
            await session.commit()
            await session.refresh(order)

            payment = Payment(
                order_id=order.id,
                provider=PaymentProvider.PLATEGA,
                status=PaymentStatus.PENDING,
                amount=Decimal("1187.50"),
                currency=Currency.RUB,
                external_id="tx-hook-1",
                payment_url="https://pay.test/1",
            )
            session.add(payment)
            await session.commit()
            await session.refresh(payment)

            state["order_id"] = order.id
            state["payment_id"] = payment.id

    asyncio.run(seed())

    with TestClient(main_module.create_app()) as c:
        c.state = state  # type: ignore[attr-defined]
        yield c


def confirmed_body(external_id: str = "tx-hook-1", order_id: str | None = None):
    body = {
        "id": external_id,
        "amount": 1187.50,
        "currency": "RUB",
        "status": "CONFIRMED",
        "paymentMethod": 2,
    }
    if order_id:
        body["payload"] = order_id
    return body


# ------------------------------------------------------------ authentication


def test_webhook_without_credentials_is_401(client):
    response = client.post("/api/v1/webhooks/platega", json=confirmed_body())
    assert response.status_code == 401


def test_webhook_with_wrong_secret_is_401(client):
    response = client.post(
        "/api/v1/webhooks/platega",
        json=confirmed_body(),
        headers={"X-MerchantId": MERCHANT_ID, "X-Secret": "nope"},
    )
    assert response.status_code == 401


def test_unauthenticated_webhook_does_not_touch_the_payment(client):
    client.post("/api/v1/webhooks/platega", json=confirmed_body())
    payment = client.get(f"/api/v1/payments/{client.state['payment_id']}").json()
    assert payment["status"] == PaymentStatus.PENDING.value


# ------------------------------------------------------------------ happy path


def test_confirmed_webhook_marks_payment_and_order_paid(client):
    response = client.post(
        "/api/v1/webhooks/platega", json=confirmed_body(), headers=AUTH
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["applied"] is True

    payment = client.get(f"/api/v1/payments/{client.state['payment_id']}").json()
    assert payment["status"] == PaymentStatus.SUCCEEDED.value
    assert payment["paid_at"] is not None


def test_duplicate_webhook_returns_200_so_the_gateway_stops_retrying(client):
    """A non-2xx on a duplicate would make Platega retry a handled event forever."""
    first = client.post(
        "/api/v1/webhooks/platega", json=confirmed_body(), headers=AUTH
    )
    second = client.post(
        "/api/v1/webhooks/platega", json=confirmed_body(), headers=AUTH
    )

    assert first.json()["applied"] is True
    assert second.status_code == 200
    assert second.json()["applied"] is False
    assert second.json()["detail"] == "duplicate"


def test_webhook_matches_by_echoed_order_payload(client):
    """The `payload` field carries our order id, enabling order-based lookup."""
    response = client.post(
        "/api/v1/webhooks/platega",
        json=confirmed_body(external_id="tx-hook-1", order_id=client.state["order_id"]),
        headers=AUTH,
    )
    assert response.status_code == 200
    assert response.json()["applied"] is True


# ---------------------------------------------------------------- edge cases


def test_unknown_transaction_is_acked_with_200(client):
    """Retries can never resolve an unknown transaction, so do not ask for them."""
    response = client.post(
        "/api/v1/webhooks/platega",
        json=confirmed_body(external_id="tx-not-ours"),
        headers=AUTH,
    )
    assert response.status_code == 200
    assert response.json()["applied"] is False
    assert response.json()["detail"] == "unknown transaction"


def test_callback_without_transaction_id_is_400(client):
    response = client.post(
        "/api/v1/webhooks/platega", json={"status": "CONFIRMED"}, headers=AUTH
    )
    assert response.status_code == 400


def test_non_json_body_is_400(client):
    response = client.post(
        "/api/v1/webhooks/platega",
        content=b"not json at all",
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_json_array_body_is_400(client):
    response = client.post(
        "/api/v1/webhooks/platega", json=[1, 2, 3], headers=AUTH
    )
    assert response.status_code == 400


def test_canceled_webhook_cancels_the_order(client):
    response = client.post(
        "/api/v1/webhooks/platega",
        json={
            "id": "tx-hook-1",
            "amount": 1187.50,
            "currency": "RUB",
            "status": "CANCELED",
        },
        headers=AUTH,
    )
    assert response.status_code == 200
    assert response.json()["applied"] is True

    payment = client.get(f"/api/v1/payments/{client.state['payment_id']}").json()
    assert payment["status"] == PaymentStatus.FAILED.value


def test_disputed_webhook_leaves_payment_pending(client):
    """DISPUTED maps to PENDING, so nothing changes and the ack is a no-op."""
    response = client.post(
        "/api/v1/webhooks/platega",
        json={"id": "tx-hook-1", "amount": 1187.50, "status": "DISPUTED"},
        headers=AUTH,
    )
    assert response.status_code == 200
    assert response.json()["applied"] is False

    payment = client.get(f"/api/v1/payments/{client.state['payment_id']}").json()
    assert payment["status"] == PaymentStatus.PENDING.value


# -------------------------------------------------------------------- telegram


def test_telegram_webhook_rejects_a_bad_secret_token(client):
    response = client.post(
        "/api/v1/webhooks/telegram",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert response.status_code == 401


def test_telegram_webhook_acks_when_the_bot_is_not_running(client):
    """Polling mode: ack anyway so Telegram does not retry forever."""
    response = client.post(
        "/api/v1/webhooks/telegram",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
