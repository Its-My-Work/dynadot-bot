"""Platega payment gateway adapter (RU: SBP / cards / crypto).

Platega authenticates with two plain headers (``X-MerchantId`` / ``X-Secret``)
rather than a request signature, and it sends those *same* headers back on its
callbacks. Webhook verification is therefore a constant-time comparison of the
incoming secret against ours — see :meth:`PlategaGateway.verify_webhook`.
"""

from __future__ import annotations

import hmac
from typing import Any, Self

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.integrations.payments.base import (
    PaymentGateway,
    PaymentIntent,
    PaymentSession,
    WebhookEvent,
)
from app.integrations.payments.exceptions import (
    PaymentConfigError,
    PaymentGatewayError,
    PaymentSignatureError,
    PaymentTransportError,
)
from app.models.enums import Currency, PaymentProvider, PaymentStatus

logger = get_logger(__name__)

CREATE_PATH = "/transaction/process"
STATUS_PATH = "/transaction/{transaction_id}"

# Platega method selectors. 2 = SBP QR, the default for RU retail.
METHOD_SBP_QR = 2
METHOD_CARD = 1
METHOD_CRYPTO = 13

#: Platega transaction statuses mapped onto our own.
#:
#: DISPUTED stays PENDING on purpose: the money is not ours yet, and Platega
#: resends CONFIRMED once the check clears. Treating a dispute as a failure
#: would cancel an order that is about to be paid.
_STATUS_MAP: dict[str, PaymentStatus] = {
    "PENDING": PaymentStatus.PENDING,
    "PROCESSING": PaymentStatus.PENDING,
    "DISPUTED": PaymentStatus.PENDING,
    "CONFIRMED": PaymentStatus.SUCCEEDED,
    "SUCCESS": PaymentStatus.SUCCEEDED,
    "CANCELED": PaymentStatus.FAILED,
    "CANCELLED": PaymentStatus.FAILED,
    "FAILED": PaymentStatus.FAILED,
    "DECLINED": PaymentStatus.FAILED,
    "ERROR": PaymentStatus.FAILED,
    "EXPIRED": PaymentStatus.EXPIRED,
    "TIMEOUT": PaymentStatus.EXPIRED,
    "REFUNDED": PaymentStatus.REFUNDED,
    "PARTIALLY_REFUNDED": PaymentStatus.REFUNDED,
    "CHARGEBACKED": PaymentStatus.REFUNDED,
}

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def map_status(raw: str | None) -> PaymentStatus:
    """Translate a Platega status string, defaulting to PENDING.

    An unknown status must never be read as success or failure — a wrong guess
    either hands out a domain for free or cancels a paid order.
    """
    if not raw:
        return PaymentStatus.PENDING
    mapped = _STATUS_MAP.get(raw.strip().upper())
    if mapped is None:
        logger.warning("unknown platega status", extra={"status": raw})
        return PaymentStatus.PENDING
    return mapped


def _parse_currency(raw: object) -> Currency | None:
    if not raw:
        return None
    try:
        return Currency(str(raw).strip().upper())
    except ValueError:
        return None


def _parse_amount(raw: object) -> float | None:
    """Pull a number out of either ``2000`` or ``"100 RUB"``."""
    if raw is None:
        return None
    if isinstance(raw, int | float):
        return float(raw)
    if isinstance(raw, dict):
        return _parse_amount(raw.get("amount"))
    text = str(raw).strip().split(" ")[0].replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


class PlategaGateway(PaymentGateway):
    """Async Platega adapter.

    The v2 API renamed the payment-link field from ``redirect`` to ``url``;
    both are accepted so a provider-side rename cannot break checkout.
    """

    provider = PaymentProvider.PLATEGA

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None

    # ------------------------------------------------------------------ setup

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._settings.platega_base_url,
                timeout=httpx.Timeout(self._settings.platega_timeout_seconds),
                headers={"User-Agent": "dynadot-bot/1.0"},
            )
            self._owns_client = True

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------ credentials

    def _credentials(self) -> tuple[str, str]:
        merchant_id = self._settings.platega_merchant_id
        secret = self._settings.platega_secret
        if not merchant_id or not secret:
            raise PaymentConfigError(
                "PLATEGA_MERCHANT_ID and PLATEGA_SECRET must be configured"
            )
        return merchant_id, secret.get_secret_value()

    def _auth_headers(self) -> dict[str, str]:
        merchant_id, secret = self._credentials()
        return {
            "X-MerchantId": merchant_id,
            "X-Secret": secret,
            "Content-Type": "application/json",
        }

    # ---------------------------------------------------------------- request

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self._client is None:
            await self.start()
        assert self._client is not None

        try:
            response = await self._client.request(
                method, path, json=json, headers=self._auth_headers()
            )
        except httpx.TimeoutException as exc:
            raise PaymentTransportError(f"platega timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise PaymentTransportError(f"platega transport error: {exc}") from exc

        if response.status_code in _RETRYABLE_STATUS:
            raise PaymentTransportError(
                f"platega temporarily unavailable ({response.status_code})",
                http_status=response.status_code,
            )
        if response.status_code >= 400:
            raise PaymentGatewayError(
                f"platega rejected the request ({response.status_code})",
                http_status=response.status_code,
                payload=_safe_json(response),
            )

        body = _safe_json(response)
        if not isinstance(body, dict):
            raise PaymentGatewayError("platega returned a non-object body")
        return body

    # ------------------------------------------------------------- operations

    async def create_payment(self, intent: PaymentIntent) -> PaymentSession:
        """Create a Platega transaction.

        ``payload`` carries our order id and comes back verbatim in the
        callback, which is how a webhook is tied to an order.
        """
        metadata: dict[str, Any] = {}
        if intent.user_id:
            # Missing userId disables Platega's anti-fraud for merchants that
            # require it, which can get the shop suspended.
            metadata["userId"] = str(intent.user_id)
        if intent.user_name:
            metadata["userName"] = intent.user_name
        if intent.client_ip:
            metadata["clientIp"] = intent.client_ip

        body: dict[str, Any] = {
            "paymentMethod": intent.method or METHOD_SBP_QR,
            "paymentDetails": {
                "amount": round(float(intent.amount), 2),
                "currency": (intent.currency or Currency.RUB).value,
            },
            "description": intent.description or f"Order {intent.order_id}",
            "payload": intent.order_id,
            "metadata": metadata,
        }

        return_url = intent.return_url or self._settings.payment_return_url
        if return_url:
            body["return"] = return_url
        failed_url = intent.failed_url or self._settings.payment_failed_url
        if failed_url:
            body["failedUrl"] = failed_url

        data = await self._request("POST", CREATE_PATH, json=body)

        external_id = data.get("transactionId") or data.get("id")
        if not external_id:
            raise PaymentGatewayError(
                "platega response carried no transaction id", payload=data
            )

        logger.info(
            "platega transaction created",
            extra={"order_id": intent.order_id, "external_id": external_id},
        )
        return PaymentSession(
            external_id=str(external_id),
            # v2 renamed `redirect` to `url`; accept either.
            payment_url=data.get("redirect") or data.get("url") or data.get("qr"),
            status=map_status(data.get("status")),
            amount=_parse_amount(data.get("paymentDetails")) or float(intent.amount),
            currency=_parse_currency(
                (data.get("paymentDetails") or {}).get("currency")
                if isinstance(data.get("paymentDetails"), dict)
                else None
            )
            or intent.currency,
            expires_in=data.get("expiresIn"),
            raw=data,
        )

    async def get_status(self, external_id: str) -> PaymentSession:
        """Poll a transaction — used by reconciliation when no webhook lands."""
        data = await self._request("GET", STATUS_PATH.format(transaction_id=external_id))
        details = data.get("paymentDetails")
        return PaymentSession(
            external_id=str(data.get("id") or external_id),
            payment_url=data.get("redirect") or data.get("url"),
            status=map_status(data.get("status")),
            amount=_parse_amount(details if details is not None else data.get("amount")),
            currency=_parse_currency(
                details.get("currency")
                if isinstance(details, dict)
                else data.get("currency")
            ),
            expires_in=data.get("expiresIn"),
            raw=data,
        )

    # ---------------------------------------------------------------- webhook

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> None:
        """Authenticate a Platega callback.

        Platega echoes ``X-MerchantId`` / ``X-Secret`` back to us instead of
        signing the body, so both must match our configured credentials.
        Comparison is constant-time to avoid leaking the secret byte by byte.
        """
        normalised = {k.lower(): v for k, v in headers.items()}
        merchant_id, secret = self._credentials()

        got_secret = normalised.get("x-secret") or ""
        got_merchant = normalised.get("x-merchantid") or ""

        if not hmac.compare_digest(got_secret, secret):
            raise PaymentSignatureError("platega webhook secret mismatch")
        # Only enforced when present: a merchant id is informational, but a
        # wrong one means the callback belongs to another shop.
        if got_merchant and not hmac.compare_digest(got_merchant, merchant_id):
            raise PaymentSignatureError("platega webhook merchant id mismatch")

    def parse_webhook(self, payload: dict[str, Any]) -> WebhookEvent:
        external_id = payload.get("id") or payload.get("transactionId")
        if not external_id:
            raise PaymentGatewayError(
                "platega callback carried no transaction id", payload=payload
            )

        raw_status = payload.get("status")
        return WebhookEvent(
            provider=self.provider,
            external_id=str(external_id),
            status=map_status(raw_status),
            amount=_parse_amount(payload.get("amount")),
            currency=_parse_currency(payload.get("currency")),
            order_id=str(payload["payload"]) if payload.get("payload") else None,
            raw_status=str(raw_status) if raw_status else None,
            raw=payload,
        )


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text[:500]}
