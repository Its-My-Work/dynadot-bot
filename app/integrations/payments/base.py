"""Provider-agnostic payment contracts.

Business logic talks to :class:`PaymentGateway` only. Platega-specific field
names, status spellings and auth headers never leak past this boundary, so a
second RU gateway (or CryptoBot) can be added without touching services.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import Currency, PaymentProvider, PaymentStatus


@dataclass(slots=True)
class PaymentIntent:
    """Everything needed to ask a gateway for a payment link.

    ``order_id`` is our own primary key and is echoed back by the gateway in
    its callback ``payload``; that is what makes webhook handling possible
    without trusting any gateway-side bookkeeping.
    """

    order_id: str
    amount: float
    currency: Currency = Currency.RUB
    description: str | None = None
    # Telegram id of the payer — RU gateways require it for anti-fraud.
    user_id: str | None = None
    user_name: str | None = None
    client_ip: str | None = None
    return_url: str | None = None
    failed_url: str | None = None
    # Provider-specific method selector (e.g. Platega 2 = SBP QR).
    method: int | None = None


@dataclass(slots=True)
class PaymentSession:
    """A created, not-yet-paid transaction on the gateway side."""

    external_id: str
    payment_url: str | None
    status: PaymentStatus
    amount: float | None = None
    currency: Currency | None = None
    expires_in: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WebhookEvent:
    """A normalised status-change callback.

    ``external_id`` is the gateway transaction id and the idempotency key:
    gateways legitimately redeliver the same event (Platega resends
    ``CONFIRMED`` after a dispute is lifted), so handlers must key off it.
    """

    provider: PaymentProvider
    external_id: str
    status: PaymentStatus
    amount: float | None = None
    currency: Currency | None = None
    # Our order id as echoed back by the gateway, when it supplies one.
    order_id: str | None = None
    raw_status: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class PaymentGateway(abc.ABC):
    """Interface every payment provider adapter implements."""

    provider: PaymentProvider

    @abc.abstractmethod
    async def create_payment(self, intent: PaymentIntent) -> PaymentSession:
        """Create a transaction and return the URL to send the customer to."""

    @abc.abstractmethod
    async def get_status(self, external_id: str) -> PaymentSession:
        """Poll the gateway — the fallback when a webhook never arrives."""

    @abc.abstractmethod
    def verify_webhook(self, headers: dict[str, str], body: bytes) -> None:
        """Authenticate a callback. Raise ``PaymentSignatureError`` if invalid."""

    @abc.abstractmethod
    def parse_webhook(self, payload: dict[str, Any]) -> WebhookEvent:
        """Map a raw callback body onto :class:`WebhookEvent`."""

    async def aclose(self) -> None:  # noqa: B027 - optional hook, not abstract
        """Release transport resources, if the adapter owns any.

        Deliberately concrete and empty: adapters without a persistent
        transport should not be forced to implement a no-op.
        """
