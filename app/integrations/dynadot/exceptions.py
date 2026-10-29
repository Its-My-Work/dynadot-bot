"""Typed exceptions for the Dynadot integration layer."""

from __future__ import annotations


class DynadotError(Exception):
    """Base class for every Dynadot failure."""

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

    def __str__(self) -> str:
        if self.code:
            return f"[{self.code}] {self.message}"
        return self.message


class DynadotConfigError(DynadotError):
    """API key / secret missing or malformed."""


class DynadotAuthError(DynadotError):
    """401/403 — bad key or signature mismatch."""


class DynadotRateLimitError(DynadotError):
    """429 — over the per-minute or concurrency limit. Retryable."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class DynadotTransportError(DynadotError):
    """Network failure, timeout or 5xx. Retryable."""


class DynadotApiError(DynadotError):
    """Business-level error: a non-zero ``code`` in the response body."""


class DomainUnavailableError(DynadotError):
    """The domain is taken.

    Raised when registration is attempted on an unavailable name — the caller
    must trigger a refund if the customer has already paid.
    """
