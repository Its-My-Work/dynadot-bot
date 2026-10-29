"""Dynadot RESTful v2 integration package."""

from __future__ import annotations

from app.integrations.dynadot.client import DynadotClient
from app.integrations.dynadot.exceptions import (
    DomainUnavailableError,
    DynadotApiError,
    DynadotAuthError,
    DynadotConfigError,
    DynadotError,
    DynadotRateLimitError,
    DynadotTransportError,
)
from app.integrations.dynadot.queue import DynadotQueue, Priority, RateLimiter
from app.integrations.dynadot.signing import (
    build_auth_headers,
    build_path_with_query,
    build_request_id,
    build_string_to_sign,
    sign_request,
)

__all__ = [
    "DomainUnavailableError",
    "DynadotApiError",
    "DynadotAuthError",
    "DynadotClient",
    "DynadotConfigError",
    "DynadotError",
    "DynadotQueue",
    "DynadotRateLimitError",
    "DynadotTransportError",
    "Priority",
    "RateLimiter",
    "build_auth_headers",
    "build_path_with_query",
    "build_request_id",
    "build_string_to_sign",
    "sign_request",
]
