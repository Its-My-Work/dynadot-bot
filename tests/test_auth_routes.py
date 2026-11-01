"""Tests for authentication, account linking, and single-use codes."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_dynadot_client
from app.main import create_app
from app.models import Base
from app.models.user import User
from app.services.security import hash_password, verify_password
from tests.test_registration_service import FakeDynadot

TELEGRAM_ID = 777000111


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "api_auth.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    import app.core.config as config_module
    import app.db.session as session_module

    config_module.get_settings.cache_clear()
    new_settings = config_module.Settings()
    monkeypatch.setattr(config_module, "settings", new_settings)
    monkeypatch.setattr(session_module, "settings", new_settings)
    monkeypatch.setattr(session_module, "_engine", None)
    monkeypatch.setattr(session_module, "_session_factory", None)

    async def setup():
        async with session_module.get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = session_module.get_session_factory()
        async with factory() as session:
            session.add(
                User(
                    telegram_id=TELEGRAM_ID,
                    username="buyer",
                    telegram_username="buyer_tg",
                )
            )
            await session.commit()

    asyncio.run(setup())

    app = create_app()
    app.dependency_overrides[get_dynadot_client] = lambda: FakeDynadot()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_password_hashing():
    raw = "SecureP@ssw0rd!"
    hashed = hash_password(raw)
    assert hashed.startswith("pbkdf2:sha256:100000$")
    assert verify_password(raw, hashed)
    assert not verify_password("WrongPassword", hashed)


def test_email_registration_and_login(client):
    # 1. Register new standalone email
    email = "testuser_dyn@example.com"
    password = "mysecretpassword123"
    reg_res = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert reg_res.status_code == 201
    data = reg_res.json()
    assert data["user"]["email"] == email
    assert data["user"]["has_password"] is True
    assert data["token"].startswith("dyn_")
    allocated_tid = data["user"]["telegram_id"]
    assert allocated_tid >= 9_000_000_000

    # 2. Duplicate registration fails
    dup_res = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert dup_res.status_code == 400

    # 3. Successful login
    login_res = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_res.status_code == 200
    assert login_res.json()["user"]["email"] == email

    # 4. Wrong password fails
    bad_login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "wrongpassword"},
    )
    assert bad_login.status_code == 401


def test_link_email_to_existing_telegram_user(client):
    # Bind email to existing telegram user
    res = client.post(
        "/api/v1/auth/link-email",
        json={
            "telegram_id": TELEGRAM_ID,
            "email": "buyer@example.com",
            "password": "Password123!",
        },
    )
    assert res.status_code == 200
    user_data = res.json()["user"]
    assert user_data["email"] == "buyer@example.com"
    assert user_data["has_password"] is True
    assert user_data["telegram_id"] == TELEGRAM_ID

    # Now login with this email works
    login_res = client.post(
        "/api/v1/auth/login",
        json={"email": "buyer@example.com", "password": "Password123!"},
    )
    assert login_res.status_code == 200
    assert login_res.json()["user"]["telegram_id"] == TELEGRAM_ID


def test_telegram_link_generation(client):
    link_res = client.post(
        f"/api/v1/auth/link-telegram/generate?telegram_id={TELEGRAM_ID}"
    )
    assert link_res.status_code == 200
    bot_url = link_res.json()["bot_url"]
    assert "t.me/itsmywork_ai_bot?start=link_" in bot_url
