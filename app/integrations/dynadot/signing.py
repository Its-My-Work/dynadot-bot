"""Request signing for the Dynadot RESTful v2 API.

Dynadot requires an ``X-Signature`` header carrying an HMAC-SHA256 over a
newline-joined string. The request body is part of that string, so the
signature must be computed *after* the body has been serialised.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from urllib.parse import urlencode


def build_request_id() -> str:
    """Create an idempotency key.

    Generate this ONCE per logical operation and persist it: ``X-Request-Id``
    is part of the signature and Dynadot treats it as the idempotency key. A
    fresh id on a registration retry risks registering and charging twice.
    """
    return uuid.uuid4().hex


def build_path_with_query(path: str, params: dict[str, object] | None = None) -> str:
    """Join path and query exactly as it will appear on the wire.

    The signed string must match the real request target character for
    character, so the same function builds both.
    """
    if not path.startswith("/"):
        path = "/" + path
    if not params:
        return path

    # Drop None values; keep caller-provided ordering for deterministic output.
    pairs = [(k, v) for k, v in params.items() if v is not None]
    if not pairs:
        return path
    normalised = [
        (k, "true" if v is True else "false" if v is False else str(v)) for k, v in pairs
    ]
    return f"{path}?{urlencode(normalised)}"


def build_string_to_sign(
    api_key: str, path_with_query: str, request_id: str, body: str = ""
) -> str:
    """Assemble the newline-joined payload that gets signed."""
    return "\n".join([api_key, path_with_query, request_id, body])


def sign_request(
    *,
    api_key: str,
    api_secret: str,
    path_with_query: str,
    request_id: str,
    body: str = "",
) -> str:
    """Return the hex HMAC-SHA256 signature for one request."""
    string_to_sign = build_string_to_sign(api_key, path_with_query, request_id, body)
    return hmac.new(
        api_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def build_auth_headers(
    *,
    api_key: str,
    api_secret: str,
    path_with_query: str,
    request_id: str,
    body: str = "",
) -> dict[str, str]:
    """Build the full set of authentication headers for a request."""
    signature = sign_request(
        api_key=api_key,
        api_secret=api_secret,
        path_with_query=path_with_query,
        request_id=request_id,
        body=body,
    )
    return {
        "Authorization": f"Bearer {api_key}",
        "X-Request-Id": request_id,
        "X-Signature": signature,
        "Accept": "application/json",
    }
