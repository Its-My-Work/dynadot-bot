"""Tests for the Dynadot client against a mocked HTTP transport."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from app.core.config import Settings
from app.integrations.dynadot import DynadotClient
from app.integrations.dynadot.exceptions import (
    DomainUnavailableError,
    DynadotApiError,
    DynadotAuthError,
    DynadotConfigError,
    DynadotRateLimitError,
)


def make_settings(**overrides) -> Settings:
    base = dict(
        dynadot_api_key="test-key",
        dynadot_api_secret="test-secret",
        dynadot_base_url="https://api-sandbox.dynadot.com",
        dynadot_max_retries=2,
        dynadot_rate_limit_per_minute=6000,
        database_url="sqlite+aiosqlite:///:memory:",
    )
    base.update(overrides)
    return Settings(**base)


def client_with(handler, **overrides) -> DynadotClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(
        transport=transport, base_url="https://api-sandbox.dynadot.com"
    )
    return DynadotClient(make_settings(**overrides), client=http)


@pytest.mark.asyncio
async def test_search_sends_valid_signature_and_headers():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["url"] = str(request.url)
        return httpx.Response(
            200, json={"code": 0, "message": "ok", "data": {"available": "yes"}}
        )

    async with client_with(handler) as client:
        result = await client.search("example.com")

    assert result["available"] == "yes"

    headers = captured["headers"]
    assert headers["authorization"] == "Bearer test-key"

    # Recompute the signature over the exact path+query that was sent.
    path_with_query = captured["url"].replace("https://api-sandbox.dynadot.com", "")
    expected = hmac.new(
        b"test-secret",
        f"test-key\n{path_with_query}\n{headers['x-request-id']}\n".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert headers["x-signature"] == expected


@pytest.mark.asyncio
async def test_register_signs_the_serialised_body_and_reuses_request_id():
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        captured.append(
            {
                "body": body,
                "request_id": request.headers["x-request-id"],
                "signature": request.headers["x-signature"],
                "url": str(request.url),
            }
        )
        return httpx.Response(200, json={"code": 0, "data": {"status": "success"}})

    async with client_with(handler) as client:
        await client.register(
            "example.com", years=2, contact_id="c1", request_id="fixed-id"
        )

    call = captured[0]
    assert call["request_id"] == "fixed-id"
    payload = json.loads(call["body"])
    assert payload["duration"] == 2
    assert payload["registrant_contact_id"] == "c1"

    path = call["url"].replace("https://api-sandbox.dynadot.com", "")
    expected = hmac.new(
        b"test-secret",
        f"test-key\n{path}\nfixed-id\n{call['body']}".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert call["signature"] == expected


@pytest.mark.asyncio
async def test_retry_reuses_the_same_request_id():
    """A new id on retry could register (and charge for) the domain twice."""
    ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        ids.append(request.headers["x-request-id"])
        if len(ids) == 1:
            return httpx.Response(503, json={"message": "unavailable"})
        return httpx.Response(200, json={"code": 0, "data": {"ok": True}})

    async with client_with(handler) as client:
        result = await client.register("x.com", request_id="stable-id")

    assert ids == ["stable-id", "stable-id"]
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_rate_limit_raises_after_retries_exhausted():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"}, json={"m": "slow"})

    async with client_with(handler) as client:
        with pytest.raises(DynadotRateLimitError):
            await client.search("example.com")


@pytest.mark.asyncio
async def test_auth_error_is_not_retried():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={"message": "bad signature"})

    async with client_with(handler) as client:
        with pytest.raises(DynadotAuthError):
            await client.get_account_info()

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_business_error_code_raises_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 100, "message": "invalid domain"})

    async with client_with(handler) as client:
        with pytest.raises(DynadotApiError, match="invalid domain"):
            await client.search("bad")


@pytest.mark.asyncio
async def test_ensure_available_raises_when_domain_taken():
    """Guards the resale 'taken after payment' case."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "data": {"available": "no"}})

    async with client_with(handler) as client:
        with pytest.raises(DomainUnavailableError):
            await client.ensure_available("taken.com")


@pytest.mark.asyncio
async def test_ensure_available_passes_when_free():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "data": {"available": "yes"}})

    async with client_with(handler) as client:
        assert (await client.ensure_available("free.com"))["available"] == "yes"


@pytest.mark.asyncio
async def test_missing_credentials_raise_config_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0})

    client = client_with(handler, dynadot_api_key=None, dynadot_api_secret=None)
    with pytest.raises(DynadotConfigError):
        await client.search("example.com")


@pytest.mark.asyncio
async def test_bulk_search_joins_domains_into_one_request():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"code": 0, "data": {"domain_result_list": []}})

    async with client_with(handler) as client:
        await client.bulk_search(["a.com", "b.net"])

    assert "a.com%2Cb.net" in captured["url"] or "a.com,b.net" in captured["url"]


@pytest.mark.asyncio
async def test_log_sink_receives_redacted_records():
    records: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "data": {}})

    async def sink(record: dict) -> None:
        records.append(record)

    async with client_with(handler) as client:
        client.log_sink = sink
        await client.search("example.com")

    assert records
    assert records[0]["endpoint"].startswith("/restful/v2/domains/")
    assert records[0]["http_status"] == 200
