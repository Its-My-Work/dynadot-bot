"""Payment integrations (Platega and future RU gateways)."""

from __future__ import annotations

from app.integrations.payments.base import (
    PaymentGateway,
    PaymentIntent,
    PaymentSession,
    WebhookEvent,
)
from app.integrations.payments.exceptions import (
    PaymentConfigError,
    PaymentError,
    PaymentGatewayError,
    PaymentNotFoundError,
    PaymentSignatureError,
    PaymentTransportError,
)
from app.integrations.payments.platega import (
    METHOD_CARD,
    METHOD_CRYPTO,
    METHOD_SBP_QR,
    PlategaGateway,
    map_status,
)

__all__ = [
    "METHOD_CARD",
    "METHOD_CRYPTO",
    "METHOD_SBP_QR",
    "PaymentConfigError",
    "PaymentError",
    "PaymentGateway",
    "PaymentGatewayError",
    "PaymentIntent",
    "PaymentNotFoundError",
    "PaymentSession",
    "PaymentSignatureError",
    "PaymentTransportError",
    "PlategaGateway",
    "WebhookEvent",
    "map_status",
]
