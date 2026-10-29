"""Tests for the app factory, routes and ORM schema."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Base, User
from app.models.enums import Language, OrderStatus


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient backed by a real on-disk SQLite database."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    # Rebuild settings and engine against the patched DSN.
    import app.core.config as config_module
    import app.db.session as session_module

    config_module.get_settings.cache_clear()
    new_settings = config_module.Settings()
    monkeypatch.setattr(config_module, "settings", new_settings)
    monkeypatch.setattr(session_module, "settings", new_settings)
    monkeypatch.setattr(session_module, "_engine", None)
    monkeypatch.setattr(session_module, "_session_factory", None)

    import asyncio

    async def create_tables():
        async with session_module.get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(create_tables())

    with TestClient(create_app()) as c:
        yield c


def test_health_endpoint_reports_database_ok(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_languages_endpoint_lists_ru_and_en(client):
    body = client.get("/api/v1/languages").json()
    assert body["default"] == "ru"
    assert set(body["supported"]) == {"ru", "en"}


@pytest.mark.parametrize(
    ("headers", "expected_lang"),
    [
        ({"Accept-Language": "ru-RU,ru;q=0.9"}, "ru"),
        ({"Accept-Language": "en-US,en;q=0.9"}, "en"),
        ({"X-Language": "en"}, "en"),
        ({"X-Language": "ru", "Accept-Language": "en-US"}, "ru"),  # explicit wins
        ({}, "ru"),
    ],
)
def test_request_language_resolution(client, headers, expected_lang):
    body = client.get("/api/v1/ping", headers=headers).json()
    assert body["language"] == expected_lang


def test_openapi_schema_is_generated(client):
    schema = client.get("/api/v1/openapi.json").json()
    paths = schema["paths"]
    for expected in (
        "/api/v1/health",
        "/api/v1/domains/search",
        "/api/v1/users/{telegram_id}/language",
        "/api/v1/admin/tld-pricing",
        "/api/v1/admin/api-logs",
    ):
        assert expected in paths, f"missing route {expected}"


def test_unknown_user_returns_localised_404(client):
    response = client.get("/api/v1/users/999999", headers={"X-Language": "en"})
    assert response.status_code == 404
    assert response.json()["detail"] == "Not found."


def test_all_expected_tables_exist():
    assert set(Base.metadata.tables) == {
        "users",
        "contact_profiles",
        "domains",
        "tld_pricing",
        "orders",
        "payments",
        "dynadot_api_logs",
        "dynadot_tasks",
        "app_settings",
        "server_instances",
    }


def test_user_display_name_prefers_username():
    assert User(telegram_id=1, username="bob").display_name == "@bob"
    full = User(telegram_id=1, first_name="Ann", last_name="Lee")
    assert full.display_name == "Ann Lee"
    assert User(telegram_id=42).display_name == "id42"


def test_order_status_includes_refund_required():
    """Required for the 'domain taken after payment' resale path."""
    assert OrderStatus.REFUND_REQUIRED.value == "refund_required"


def test_default_language_is_russian():
    assert Language.RU.value == "ru"
