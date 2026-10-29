"""Tests for the Platega gateway adapter."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.integrations.payments import (
    METHOD_SBP_QR,
    PaymentConfigError,
    PaymentGatewayError,
    PaymentIntent,
    PaymentSignatureError,
    PaymentTransportError,
    PlategaGateway,
    map_status,
)
from app.models.enums import Currency, PaymentProvider, PaymentStatus

MERCHANT_ID = "1a021d91-9b26-4762-b303-5d4aac74e921"
SECRET = "super-secret-key"


def make_settings(**overrides) -> Settings:
    base = {
        "platega_merchant_id": MERCHANT_ID,
        "platega_secret": SECRET,
        "platega_base_url": "https://app.platega.test",
    }
    base.update(overrides)
    return Settings(**base)


def make_gateway(handler, **overrides) -> PlategaGateway:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        transport=transport, base_url="https://app.platega.test"
    )
    return PlategaGateway(make_settings(**overrides), client=client)


# ------------------------------------------------------------- status mapping


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CONFIRMED", PaymentStatus.SUCCEEDED),
        ("confirmed", PaymentStatus.SUCCEEDED),
        ("PENDING", PaymentStatus.PENDING),
        ("CANCELED", PaymentStatus.FAILED),
        ("CANCELLED", PaymentStatus.FAILED),
        ("EXPIRED", PaymentStatus.EXPIRED),
        ("REFUNDED", PaymentStatus.REFUNDED),
        ("CHARGEBACKED", PaymentStatus.REFUNDED),
    ],
)
def test_map_status_translates_known_statuses(raw, expected):
    assert map_status(raw) == expected


def test_disputed_stays_pending_so_money_is_not_assumed_ours():
    """A dispute is not a failure: Platega resends CONFIRMED once it clears."""
    assert map_status("DISPUTED") == PaymentStatus.PENDING


def test_unknown_status_defaults_to_pending_never_succeeded():
    """Guessing success on an unknown status would hand out a free domain."""
    assert map_status("SOMETHING_NEW") == PaymentStatus.PENDING
    assert map_status(None) == PaymentStatus.PENDING


# --------------------------------------------------------------- credentials


@pytest.mark.asyncio
async def test_missing_credentials_raise_config_error():
    gateway = PlategaGateway(
        Settings(platega_merchant_id=None, platega_secret=None),
        client=httpx.AsyncClient(),
    )
    with pytest.raises(PaymentConfigError):
        await gateway.create_payment(PaymentIntent(order_id="o1", amount=100))
    await gateway.aclose()


# ------------------------------------------------------------ create_payment


@pytest.mark.asyncio
async def test_create_payment_sends_auth_headers_and_order_payload():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "paymentMethod": "SBPQR",
                "transactionId": "3fa85f64-5717-4562-b3fc-2c463f66afa6",
                "redirect": "https://pay.platega.io?qrsbp",
                "status": "PENDING",
                "expiresIn": "00:15:00",
                "paymentDetails": {"amount": 1500, "currency": "RUB"},
            },
        )

    gateway = make_gateway(handler)
    session = await gateway.create_payment(
        PaymentIntent(
            order_id="order-8089",
            amount=1500,
            user_id="123456789",
            user_name="@tester",
            client_ip="111.47.86.11",
        )
    )

    assert captured["url"].endswith("/transaction/process")
    assert captured["headers"]["x-merchantid"] == MERCHANT_ID
    assert captured["headers"]["x-secret"] == SECRET

    body = captured["body"]
    assert body["paymentMethod"] == METHOD_SBP_QR
    assert body["paymentDetails"] == {"amount": 1500.0, "currency": "RUB"}
    # payload carries our order id and is echoed back in the callback.
    assert body["payload"] == "order-8089"
    assert body["metadata"]["userId"] == "123456789"
    assert body["metadata"]["clientIp"] == "111.47.86.11"

    assert session.external_id == "3fa85f64-5717-4562-b3fc-2c463f66afa6"
    assert session.payment_url == "https://pay.platega.io?qrsbp"
    assert session.status == PaymentStatus.PENDING
    assert session.amount == 1500.0
    assert session.currency == Currency.RUB
    await gateway.aclose()


@pytest.mark.asyncio
async def test_create_payment_accepts_v2_url_field_instead_of_redirect():
    """v2 renamed `redirect` to `url`; both must keep checkout working."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "transactionId": "tx-1",
                "url": "https://pay.platega.io/v2",
                "status": "PENDING",
            },
        )

    gateway = make_gateway(handler)
    session = await gateway.create_payment(PaymentIntent(order_id="o1", amount=10))
    assert session.payment_url == "https://pay.platega.io/v2"
    await gateway.aclose()


@pytest.mark.asyncio
async def test_create_payment_without_transaction_id_is_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "PENDING"})

    gateway = make_gateway(handler)
    with pytest.raises(PaymentGatewayError):
        await gateway.create_payment(PaymentIntent(order_id="o1", amount=10))
    await gateway.aclose()


@pytest.mark.asyncio
async def test_create_payment_omits_metadata_fields_that_were_not_supplied():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"transactionId": "t", "status": "PENDING"})

    gateway = make_gateway(handler)
    await gateway.create_payment(PaymentIntent(order_id="o1", amount=10))
    assert captured["body"]["metadata"] == {}
    # No return url configured, so the key must be absent entirely.
    assert "return" not in captured["body"]
    await gateway.aclose()


@pytest.mark.asyncio
async def test_return_and_failed_urls_come_from_settings():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"transactionId": "t", "status": "PENDING"})

    gateway = make_gateway(
        handler,
        payment_return_url="https://shop.test/ok",
        payment_failed_url="https://shop.test/fail",
    )
    await gateway.create_payment(PaymentIntent(order_id="o1", amount=10))
    assert captured["body"]["return"] == "https://shop.test/ok"
    assert captured["body"]["failedUrl"] == "https://shop.test/fail"
    await gateway.aclose()


# ------------------------------------------------------------------- errors


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
async def test_transient_status_codes_raise_transport_error(code):
    """These must be retryable, not a permanent gateway rejection."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, json={"error": "nope"})

    gateway = make_gateway(handler)
    with pytest.raises(PaymentTransportError):
        await gateway.create_payment(PaymentIntent(order_id="o1", amount=10))
    await gateway.aclose()


@pytest.mark.asyncio
async def test_client_error_raises_gateway_error_with_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "bad amount"})

    gateway = make_gateway(handler)
    with pytest.raises(PaymentGatewayError) as exc_info:
        await gateway.create_payment(PaymentIntent(order_id="o1", amount=10))
    assert exc_info.value.http_status == 400
    assert exc_info.value.payload["message"] == "bad amount"
    await gateway.aclose()


@pytest.mark.asyncio
async def test_timeout_raises_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    gateway = make_gateway(handler)
    with pytest.raises(PaymentTransportError):
        await gateway.create_payment(PaymentIntent(order_id="o1", amount=10))
    await gateway.aclose()


# ------------------------------------------------------------------- status


@pytest.mark.asyncio
async def test_get_status_parses_nested_payment_details():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/transaction/tx-7"
        return httpx.Response(
            200,
            json={
                "id": "tx-7",
                "status": "CONFIRMED",
                "paymentDetails": {"amount": 2000, "currency": "RUB"},
                "expiresIn": "00:15:00",
            },
        )

    gateway = make_gateway(handler)
    session = await gateway.get_status("tx-7")
    assert session.status == PaymentStatus.SUCCEEDED
    assert session.amount == 2000.0
    assert session.currency == Currency.RUB
    await gateway.aclose()


@pytest.mark.asyncio
async def test_get_status_parses_string_amount_with_currency_suffix():
    """Some Platega responses render the amount as `"100 RUB"`."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"id": "tx-8", "status": "PENDING", "paymentDetails": "100 RUB"}
        )

    gateway = make_gateway(handler)
    session = await gateway.get_status("tx-8")
    assert session.amount == 100.0
    await gateway.aclose()


# ------------------------------------------------------------ webhook verify


def test_verify_webhook_accepts_matching_credentials():
    gateway = PlategaGateway(make_settings())
    gateway.verify_webhook(
        {"X-MerchantId": MERCHANT_ID, "X-Secret": SECRET}, b"{}"
    )  # must not raise


def test_verify_webhook_is_case_insensitive_on_header_names():
    gateway = PlategaGateway(make_settings())
    gateway.verify_webhook({"x-merchantid": MERCHANT_ID, "x-secret": SECRET}, b"{}")


def test_verify_webhook_rejects_wrong_secret():
    gateway = PlategaGateway(make_settings())
    with pytest.raises(PaymentSignatureError):
        gateway.verify_webhook(
            {"X-MerchantId": MERCHANT_ID, "X-Secret": "wrong"}, b"{}"
        )


def test_verify_webhook_rejects_missing_secret():
    gateway = PlategaGateway(make_settings())
    with pytest.raises(PaymentSignatureError):
        gateway.verify_webhook({"X-MerchantId": MERCHANT_ID}, b"{}")


def test_verify_webhook_rejects_another_shops_merchant_id():
    gateway = PlategaGateway(make_settings())
    with pytest.raises(PaymentSignatureError):
        gateway.verify_webhook(
            {"X-MerchantId": "someone-else", "X-Secret": SECRET}, b"{}"
        )


# ------------------------------------------------------------- webhook parse


def test_parse_webhook_maps_confirmed_callback():
    gateway = PlategaGateway(make_settings())
    event = gateway.parse_webhook(
        {
            "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
            "amount": 1500.25,
            "currency": "RUB",
            "status": "CONFIRMED",
            "paymentMethod": 2,
            "payload": "order-8089",
        }
    )
    assert event.provider == PaymentProvider.PLATEGA
    assert event.external_id == "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    assert event.status == PaymentStatus.SUCCEEDED
    assert event.amount == 1500.25
    assert event.currency == Currency.RUB
    # The echoed payload is what ties the callback back to our order.
    assert event.order_id == "order-8089"
    assert event.raw_status == "CONFIRMED"


def test_parse_webhook_handles_canceled_without_payload():
    gateway = PlategaGateway(make_settings())
    event = gateway.parse_webhook(
        {
            "id": "00000000-0000-0000-0000-000000000000",
            "amount": 1000,
            "currency": "RUB",
            "status": "CANCELED",
            "paymentMethod": 2,
        }
    )
    assert event.status == PaymentStatus.FAILED
    assert event.order_id is None


def test_parse_webhook_without_id_raises():
    gateway = PlategaGateway(make_settings())
    with pytest.raises(PaymentGatewayError):
        gateway.parse_webhook({"status": "CONFIRMED"})
