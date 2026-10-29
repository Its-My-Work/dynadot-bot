"""Tests for Dynadot HMAC-SHA256 request signing."""

from __future__ import annotations

import hashlib
import hmac

from app.integrations.dynadot.signing import (
    build_auth_headers,
    build_path_with_query,
    build_request_id,
    build_string_to_sign,
    sign_request,
)

API_KEY = "test-key"
API_SECRET = "test-secret"


def test_string_to_sign_is_newline_joined_in_documented_order():
    result = build_string_to_sign(API_KEY, "/restful/v2/domains", "req-1", '{"a":1}')
    assert result == 'test-key\n/restful/v2/domains\nreq-1\n{"a":1}'


def test_signature_matches_manual_hmac_sha256():
    path = "/restful/v2/domains/example.com/search"
    request_id = "abc123"
    body = ""

    expected = hmac.new(
        API_SECRET.encode(),
        f"{API_KEY}\n{path}\n{request_id}\n{body}".encode(),
        hashlib.sha256,
    ).hexdigest()

    assert (
        sign_request(
            api_key=API_KEY,
            api_secret=API_SECRET,
            path_with_query=path,
            request_id=request_id,
            body=body,
        )
        == expected
    )


def test_signature_covers_the_body():
    """A different body must produce a different signature."""
    kwargs = dict(
        api_key=API_KEY,
        api_secret=API_SECRET,
        path_with_query="/restful/v2/domains/x.com/register",
        request_id="r1",
    )
    assert sign_request(**kwargs, body='{"duration":1}') != sign_request(
        **kwargs, body='{"duration":2}'
    )


def test_signature_changes_with_request_id():
    kwargs = dict(
        api_key=API_KEY,
        api_secret=API_SECRET,
        path_with_query="/restful/v2/domains",
        body="",
    )
    assert sign_request(**kwargs, request_id="a") != sign_request(
        **kwargs, request_id="b"
    )


def test_build_path_with_query_is_deterministic_and_bool_normalised():
    path = build_path_with_query(
        "/restful/v2/domains/x.com/search", {"show_price": True, "currency": "USD"}
    )
    assert path == "/restful/v2/domains/x.com/search?show_price=true&currency=USD"


def test_build_path_with_query_omits_none_values():
    assert build_path_with_query("/a", {"x": None}) == "/a"
    assert build_path_with_query("/a", None) == "/a"


def test_build_path_adds_leading_slash():
    assert build_path_with_query("a/b").startswith("/a/b")


def test_auth_headers_contain_all_required_fields():
    headers = build_auth_headers(
        api_key=API_KEY,
        api_secret=API_SECRET,
        path_with_query="/restful/v2/accounts/info",
        request_id="req-9",
        body="",
    )
    assert headers["Authorization"] == f"Bearer {API_KEY}"
    assert headers["X-Request-Id"] == "req-9"
    assert len(headers["X-Signature"]) == 64  # sha256 hex
    assert headers["Accept"] == "application/json"


def test_request_ids_are_unique():
    assert len({build_request_id() for _ in range(100)}) == 100
