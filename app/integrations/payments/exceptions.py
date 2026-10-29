"""Typed exceptions for the payment integration layer."""

from __future__ import annotations


class PaymentError(Exception):
    """Base class for every payment failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        payload: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.payload = payload or {}


class PaymentConfigError(PaymentError):
    """Credentials or base URL are missing from configuration."""


class PaymentGatewayError(PaymentError):
    """The gateway answered with an error or an unusable body."""


class PaymentTransportError(PaymentError):
    """Network-level failure (timeout, DNS, connection reset)."""


class PaymentSignatureError(PaymentError):
    """A webhook failed authentication — the request is not from the gateway."""


class PaymentNotFoundError(PaymentError):
    """No local payment matches the gateway's transaction id."""
