"""Shared HTTP plumbing for the real VPS providers.

All three upstreams are plain JSON REST APIs behind a bearer token, so the
client lifecycle, timeout handling and error translation are identical. Only
the paths and the response shapes differ, and those live in the subclasses.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger
from app.integrations.vps.base import (
    BaseVpsProvider,
    VpsApiError,
    VpsConfigError,
    VpsTransportError,
)

logger = get_logger(__name__)


class HttpVpsProvider(BaseVpsProvider):
    """A ``BaseVpsProvider`` backed by an httpx client."""

    base_url: str = ""

    def __init__(
        self,
        *,
        api_token: str | None,
        base_url: str | None = None,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = api_token
        self._base_url = (base_url or self.base_url).rstrip("/")
        self._timeout = timeout
        # An injected client is how the tests drive these adapters against a
        # MockTransport without monkeypatching httpx globally.
        self._client = client
        self._owns_client = client is None

    # ----------------------------------------------------------------- client

    def _auth_headers(self) -> dict[str, str]:
        if not self._token:
            raise VpsConfigError(
                f"{type(self).__name__} requires an API token to be configured"
            )
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url, timeout=self._timeout
            )
            self._owns_client = True

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    # ---------------------------------------------------------------- request

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Call the provider and return decoded JSON (``None`` for 204)."""
        headers = self._auth_headers()
        if self._client is None:
            await self.start()
        assert self._client is not None

        url = path if path.startswith("http") else f"{self._base_url}{path}"
        try:
            response = await self._client.request(
                method, url, json=json, params=params, headers=headers
            )
        except httpx.TimeoutException as exc:
            raise VpsTransportError(f"{method} {path} timed out") from exc
        except httpx.HTTPError as exc:
            raise VpsTransportError(f"{method} {path} failed: {exc}") from exc

        if response.status_code == 404:
            raise VpsApiError(f"{method} {path} not found", status_code=404)
        if response.status_code >= 400:
            raise VpsApiError(
                f"{method} {path} returned {response.status_code}: "
                f"{response.text[:300]}",
                status_code=response.status_code,
            )
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise VpsApiError(f"{method} {path} returned non-JSON body") from exc


def as_int(value: Any) -> int | None:
    """Best-effort int coercion — providers mix strings and numbers freely."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def first_ipv4(candidates: Any) -> str | None:
    """Pull an IPv4 address out of the many shapes providers use for it.

    Timeweb returns a list of dicts, Aeza a dict keyed by family, PQ.Hosting a
    bare string — and during provisioning any of them can be empty.
    """
    if not candidates:
        return None
    if isinstance(candidates, str):
        return candidates or None
    if isinstance(candidates, dict):
        for key in ("ipv4", "ip4", "ip", "address"):
            found = first_ipv4(candidates.get(key))
            if found:
                return found
        return None
    if isinstance(candidates, list):
        for item in candidates:
            if isinstance(item, str):
                if item.count(".") == 3:
                    return item
                continue
            if isinstance(item, dict):
                family = str(item.get("type") or item.get("version") or "ipv4").lower()
                if "6" in family:
                    continue
                found = first_ipv4(item)
                if found:
                    return found
    return None


__all__ = ["HttpVpsProvider", "as_int", "first_ipv4"]
