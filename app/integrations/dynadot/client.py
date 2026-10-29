"""Async Dynadot RESTful v2 client.

Everything here is an adapter: the v2 API is officially Beta, so business logic
must never touch raw response shapes directly. All calls funnel through a
serialising queue because Dynadot processes one request at a time.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Any, Literal, Self

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.integrations.dynadot.exceptions import (
    DomainUnavailableError,
    DynadotApiError,
    DynadotAuthError,
    DynadotConfigError,
    DynadotRateLimitError,
    DynadotTransportError,
)
from app.integrations.dynadot.queue import DynadotQueue, Priority
from app.integrations.dynadot.signing import (
    build_auth_headers,
    build_path_with_query,
    build_request_id,
)

logger = get_logger(__name__)

API_PREFIX = "/restful/v2"
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_SENSITIVE_KEYS = {"authorization", "x-signature", "api_key", "secret", "password"}

HttpMethod = Literal["GET", "POST", "PUT", "DELETE"]


def _redact(data: Any) -> Any:
    """Strip secrets before anything reaches the log table."""
    if isinstance(data, dict):
        return {
            k: ("***" if k.lower() in _SENSITIVE_KEYS else _redact(v))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_redact(item) for item in data]
    return data


class DynadotClient:
    """Thin, fully-typed wrapper over the Dynadot REST v2 endpoints."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        queue: DynadotQueue | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None
        self._queue = queue or DynadotQueue(
            max_per_minute=self._settings.dynadot_rate_limit_per_minute,
            concurrency=self._settings.dynadot_max_concurrency,
        )
        # Set by the caller to persist DynadotApiLog rows without creating a
        # circular dependency on the DB layer.
        self.log_sink: Any | None = None

    # ------------------------------------------------------------------ setup

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._settings.dynadot_base_url,
                timeout=httpx.Timeout(self._settings.dynadot_timeout_seconds),
                headers={"User-Agent": "dynadot-bot/1.0"},
            )
            self._owns_client = True
        await self._queue.start()

    async def aclose(self) -> None:
        await self._queue.stop()
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    @property
    def queue(self) -> DynadotQueue:
        return self._queue

    # ---------------------------------------------------------------- request

    def _credentials(self) -> tuple[str, str]:
        key, secret = (
            self._settings.dynadot_api_key,
            self._settings.dynadot_api_secret,
        )
        if not key or not secret:
            raise DynadotConfigError(
                "DYNADOT_API_KEY / DYNADOT_API_SECRET are not configured"
            )
        return key.get_secret_value(), secret.get_secret_value()

    async def _request(
        self,
        method: HttpMethod,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        request_id: str | None = None,
        priority: Priority | int = Priority.NORMAL,
    ) -> dict[str, Any]:
        """Queue, sign, send and retry a single API call."""

        async def _operation() -> dict[str, Any]:
            return await self._send_with_retries(
                method, path, params=params, body=body, request_id=request_id
            )

        return await self._queue.submit(
            _operation, priority=priority, name=f"{method} {path}"
        )

    async def _send_with_retries(
        self,
        method: HttpMethod,
        path: str,
        *,
        params: dict[str, Any] | None,
        body: dict[str, Any] | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        api_key, api_secret = self._credentials()
        if self._client is None:
            await self.start()
        assert self._client is not None

        full_path = f"{API_PREFIX}{path if path.startswith('/') else '/' + path}"
        path_with_query = build_path_with_query(full_path, params)
        # Serialise the body once: the signature covers these exact bytes.
        body_str = json.dumps(body, separators=(",", ":")) if body else ""
        # Reuse a caller-supplied id across retries to stay idempotent.
        req_id = request_id or build_request_id()

        last_error: Exception | None = None
        for attempt in range(1, self._settings.dynadot_max_retries + 1):
            headers = build_auth_headers(
                api_key=api_key,
                api_secret=api_secret,
                path_with_query=path_with_query,
                request_id=req_id,
                body=body_str,
            )
            if body_str:
                headers["Content-Type"] = "application/json"

            started = time.monotonic()
            try:
                response = await self._client.request(
                    method,
                    path_with_query,
                    headers=headers,
                    content=body_str.encode("utf-8") if body_str else None,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = DynadotTransportError(
                    f"transport failure: {exc}", code="transport_error"
                )
                await self._log_call(
                    full_path, method, req_id, None, attempt,
                    time.monotonic() - started, body, None, str(exc),
                )
                if attempt < self._settings.dynadot_max_retries:
                    await self._backoff(attempt)
                    continue
                raise last_error from exc

            duration = time.monotonic() - started
            payload = self._parse_body(response)
            await self._log_call(
                full_path, method, req_id, response.status_code, attempt,
                duration, body, payload, None,
            )

            if response.status_code in _RETRYABLE_STATUS:
                retry_after = self._retry_after(response)
                last_error = (
                    DynadotRateLimitError(
                        "rate limited by Dynadot",
                        retry_after=retry_after,
                        http_status=response.status_code,
                        code="rate_limited",
                    )
                    if response.status_code == 429
                    else DynadotTransportError(
                        f"server error {response.status_code}",
                        http_status=response.status_code,
                        code="server_error",
                    )
                )
                if attempt < self._settings.dynadot_max_retries:
                    await self._backoff(attempt, retry_after)
                    continue
                raise last_error

            if response.status_code in (401, 403):
                raise DynadotAuthError(
                    "authentication or signature rejected",
                    http_status=response.status_code,
                    code="auth_error",
                    payload=payload,
                )

            if response.status_code >= 400:
                raise DynadotApiError(
                    self._message(payload) or f"HTTP {response.status_code}",
                    http_status=response.status_code,
                    code=str(payload.get("code") or response.status_code),
                    payload=payload,
                )

            return self._unwrap(payload)

        raise last_error or DynadotTransportError("request failed", code="unknown")

    # ------------------------------------------------------------- primitives

    @staticmethod
    def _parse_body(response: httpx.Response) -> dict[str, Any]:
        try:
            parsed = response.json()
            return parsed if isinstance(parsed, dict) else {"data": parsed}
        except (json.JSONDecodeError, ValueError):
            return {"raw": response.text[:2000]}

    @staticmethod
    def _message(payload: dict[str, Any]) -> str | None:
        value = payload.get("message") or payload.get("error")
        return str(value) if value else None

    def _unwrap(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate the ``{code, message, data}`` envelope and return ``data``."""
        code = payload.get("code")
        # Dynadot signals success with code 0 / "0" / 200.
        if code not in (None, 0, "0", 200, "200"):
            message = self._message(payload) or "Dynadot API error"
            raise DynadotApiError(message, code=str(code), payload=payload)

        data = payload.get("data")
        if isinstance(data, dict):
            return data
        if data is not None:
            return {"data": data}
        return payload

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    async def _backoff(self, attempt: int, retry_after: float | None = None) -> None:
        """Exponential backoff with jitter, honouring ``Retry-After``."""
        delay = retry_after if retry_after else min(2 ** (attempt - 1), 30)
        delay += random.uniform(0, 0.5)
        logger.warning(
            "retrying dynadot call", extra={"attempt": attempt, "delay_s": delay}
        )
        await asyncio.sleep(delay)

    async def _log_call(
        self,
        endpoint: str,
        method: str,
        request_id: str,
        http_status: int | None,
        attempt: int,
        duration_s: float,
        request_body: dict[str, Any] | None,
        response_body: dict[str, Any] | None,
        error: str | None,
    ) -> None:
        record = {
            "endpoint": endpoint,
            "method": method,
            "request_id": request_id,
            "http_status": http_status,
            "attempt": attempt,
            "duration_ms": round(duration_s * 1000, 2),
            "request_payload": _redact(request_body),
            "response_payload": _redact(response_body),
            "error_message": error,
        }
        logger.info("dynadot call", extra=record)
        if self.log_sink is not None:
            try:
                await self.log_sink(record)
            except Exception:
                logger.exception("failed to persist dynadot api log")

    # ------------------------------------------------------------- endpoints

    async def search(self, domain: str, *, show_price: bool = True) -> dict[str, Any]:
        """Availability for a single domain."""
        return await self._request(
            "GET",
            f"/domains/{domain}/search",
            params={"show_price": show_price, "currency": "USD"},
            priority=Priority.LOW,
        )

    async def bulk_search(self, domains: list[str]) -> dict[str, Any]:
        """Availability for many domains — always prefer this over N searches."""
        return await self._request(
            "GET",
            "/domains/bulk_search",
            params={"domain_name_list": ",".join(domains), "show_price": True},
            priority=Priority.LOW,
        )

    async def get_tld_prices(self, currency: str = "USD") -> dict[str, Any]:
        return await self._request(
            "GET",
            "/domains/get_tld_price",
            params={"currency": currency},
            priority=Priority.LOW,
        )

    async def register(
        self,
        domain: str,
        *,
        years: int = 1,
        contact_id: str | None = None,
        nameservers: list[str] | None = None,
        request_id: str,
        privacy: bool = True,
    ) -> dict[str, Any]:
        """Register a domain.

        ``request_id`` is mandatory and must be the persisted, reused value for
        this order — see ``build_request_id``.
        """
        body: dict[str, Any] = {"duration": years, "privacy": privacy}
        if contact_id:
            body["registrant_contact_id"] = contact_id
        if nameservers:
            body["nameserver_list"] = nameservers

        return await self._request(
            "POST",
            f"/domains/{domain}/register",
            body=body,
            request_id=request_id,
            priority=Priority.CRITICAL,
        )

    async def renew(
        self, domain: str, *, years: int = 1, request_id: str
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/domains/{domain}/renew",
            body={"duration": years},
            request_id=request_id,
            priority=Priority.CRITICAL,
        )

    async def get_domain_info(self, domain: str) -> dict[str, Any]:
        return await self._request("GET", f"/domains/{domain}")

    async def list_domains(self) -> dict[str, Any]:
        return await self._request("GET", "/domains")

    async def set_nameservers(
        self, domain: str, nameservers: list[str], *, request_id: str | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "PUT",
            f"/domains/{domain}/nameservers",
            body={"nameserver_list": nameservers},
            request_id=request_id,
            priority=Priority.HIGH,
        )

    async def set_dns_records(
        self, domain: str, records: list[dict[str, Any]], *, request_id: str | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "PUT",
            f"/domains/{domain}/records",
            body={"record_list": records},
            request_id=request_id,
            priority=Priority.HIGH,
        )

    async def create_contact(
        self, contact: dict[str, Any], *, request_id: str
    ) -> dict[str, Any]:
        """Create a WHOIS contact; persist the returned id and reuse it."""
        return await self._request(
            "POST",
            "/contacts",
            body=contact,
            request_id=request_id,
            priority=Priority.HIGH,
        )

    async def get_account_info(self) -> dict[str, Any]:
        """Account balance — a drained balance fails every registration."""
        return await self._request("GET", "/accounts/info")

    # --------------------------------------------------------------- helpers

    async def ensure_available(self, domain: str) -> dict[str, Any]:
        """Re-check availability immediately before registering.

        Between taking payment and calling register, someone else can take the
        name. Callers must map ``DomainUnavailableError`` to a refund.
        """
        result = await self.search(domain)
        available = result.get("available")
        if isinstance(available, str):
            available = available.lower() in ("yes", "true", "1")
        if not available:
            raise DomainUnavailableError(
                f"{domain} is no longer available",
                code="domain_taken",
                payload=result,
            )
        return result
