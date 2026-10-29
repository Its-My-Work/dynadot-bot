"""Inbound webhook endpoints (payment gateways, Telegram).

Webhook rules enforced here:

* **Authenticate before parsing.** An unauthenticated body is never allowed to
  touch the database.
* **Read the raw body for verification.** Re-serialising a parsed model changes
  bytes and would break any body-based signature scheme.
* **Return 2xx for anything we have decided about.** Gateways retry on non-2xx,
  so a duplicate or an already-final payment must still answer 200 — otherwise
  the provider hammers us forever over an event we deliberately ignored.
* **Return 5xx only for genuinely transient failures**, so the retry is useful.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.deps import DbSession, Payments
from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.payments.exceptions import (
    PaymentConfigError,
    PaymentGatewayError,
    PaymentNotFoundError,
    PaymentSignatureError,
)
from app.models.enums import OrderStatus
from app.schemas import WebhookAck
from app.services.payments import PaymentService
from app.services.worker import enqueue_registration

logger = get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/platega", response_model=WebhookAck)
async def platega_webhook(
    request: Request,
    response: Response,
    session: DbSession,
    gateway: Payments,
) -> WebhookAck:
    """Handle a Platega transaction status callback."""
    raw_body = await request.body()

    try:
        gateway.verify_webhook(dict(request.headers), raw_body)
    except PaymentSignatureError as exc:
        # Deliberately vague: a precise message helps an attacker probe.
        logger.warning("platega webhook rejected", extra={"error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
        ) from exc
    except PaymentConfigError as exc:
        logger.error("platega webhook received but gateway unconfigured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="payment gateway is not configured",
        ) from exc

    payload = _parse_json(raw_body)

    try:
        event = gateway.parse_webhook(payload)
    except PaymentGatewayError as exc:
        # Malformed and authenticated: retrying cannot help, so ack with 400.
        logger.warning("platega webhook unparsable", extra={"error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="malformed callback"
        ) from exc

    service = PaymentService(session, gateway, settings)
    try:
        result = await service.apply_webhook(event)
    except PaymentNotFoundError:
        # 200: the transaction is not ours (or not yet persisted). Retries
        # would never succeed, and a 4xx makes the gateway retry regardless.
        logger.warning(
            "platega webhook for unknown transaction",
            extra={"external_id": event.external_id},
        )
        return WebhookAck(ok=True, applied=False, detail="unknown transaction")

    if not result.changed:
        response.status_code = status.HTTP_200_OK
        return WebhookAck(ok=True, applied=False, detail=result.reason)

    # A newly paid order must get its registration task before we ack, so a
    # crash straight after the 200 still leaves durable work to pick up.
    if (
        result.order is not None
        and result.order.status is OrderStatus.PAID
    ):
        try:
            await enqueue_registration(session, result.order)
        except Exception:
            # Never fail an applied payment over the queue: the worker's
            # orphan sweep re-adopts any PAID order that has no task.
            logger.exception(
                "failed to enqueue registration",
                extra={"order_id": result.order.id},
            )

    return WebhookAck(ok=True, applied=True, detail=result.payment.status.value)


@router.post("/telegram", include_in_schema=False)
async def telegram_webhook(request: Request) -> dict[str, bool]:
    """Receive Telegram updates when running in webhook mode.

    Telegram authenticates itself with a secret token header set at
    ``setWebhook`` time; a mismatch means the request is not from Telegram.
    """
    expected = settings.telegram_webhook_secret
    if expected is not None:
        provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if provided != expected.get_secret_value():
            logger.warning("telegram webhook rejected: bad secret token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token"
            )

    dispatcher = getattr(request.app.state, "bot_dispatcher", None)
    bot = getattr(request.app.state, "bot", None)
    if dispatcher is None or bot is None:
        # Polling mode (or bot disabled): ack so Telegram stops retrying.
        logger.info("telegram webhook received but bot is not running")
        return {"ok": True}

    from aiogram.types import Update

    update = Update.model_validate(
        _parse_json(await request.body()), context={"bot": bot}
    )
    await dispatcher.feed_update(bot, update)
    return {"ok": True}


def _parse_json(raw: bytes) -> dict[str, Any]:
    import json

    try:
        data = json.loads(raw or b"{}")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="body is not JSON"
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="body must be an object"
        )
    return data
