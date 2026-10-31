"""API tests for the orders, contacts, domain-management and admin routes.

These run against a real SQLite database through the real app, with only the
Dynadot client faked — the point is to catch wiring and serialisation bugs that
unit tests on the services cannot see.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_dynadot_client
from app.main import create_app
from app.models import Base
from app.models.domain import Domain, TldPricing
from app.models.enums import DomainStatus, OrderStatus, TaskStatus
from app.models.order import Order
from app.models.user import User
from tests.test_registration_service import FakeDynadot

TELEGRAM_ID = 777000111

CONTACT_PAYLOAD = {
    "first_name": "Ivan",
    "last_name": "Petrov",
    "email": "ivan@example.com",
    "phone_country_code": "7",
    "phone_number": "9990001122",
    "address_line1": "Lenina 1",
    "city": "Moscow",
    "zip_code": "101000",
    "country": "ru",
}


@pytest.fixture
def fake_dynadot():
    client = FakeDynadot()
    # Search results carry a price so order creation can quote.
    return client


@pytest.fixture
def client(tmp_path, monkeypatch, fake_dynadot):
    db_path = tmp_path / "api.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    import app.core.config as config_module
    import app.db.session as session_module

    config_module.get_settings.cache_clear()
    new_settings = config_module.Settings()
    monkeypatch.setattr(config_module, "settings", new_settings)
    monkeypatch.setattr(session_module, "settings", new_settings)
    monkeypatch.setattr(session_module, "_engine", None)
    monkeypatch.setattr(session_module, "_session_factory", None)

    for module in (
        "app.api.routes.orders",
        "app.api.routes.domains",
        "app.api.routes.admin",
    ):
        import importlib

        monkeypatch.setattr(
            importlib.import_module(module), "settings", new_settings, raising=False
        )

    async def setup():
        async with session_module.get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = session_module.get_session_factory()
        async with factory() as session:
            session.add(User(telegram_id=TELEGRAM_ID, username="buyer"))
            await session.commit()

    asyncio.run(setup())

    app = create_app()
    app.dependency_overrides[get_dynadot_client] = lambda: fake_dynadot
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _session_factory():
    import app.db.session as session_module

    return session_module.get_session_factory()


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ contacts


def test_first_contact_is_automatically_the_default(client):
    """Zero-tap checkout depends on there always being a default profile."""
    response = client.post(
        "/api/v1/contacts",
        json=CONTACT_PAYLOAD,
        params={"telegram_id": TELEGRAM_ID},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["is_default"] is True
    assert body["country"] == "RU"  # normalised upper-case


def test_adding_a_default_contact_demotes_the_previous_one(client):
    client.post(
        "/api/v1/contacts", json=CONTACT_PAYLOAD, params={"telegram_id": TELEGRAM_ID}
    )
    second = {**CONTACT_PAYLOAD, "first_name": "Maria", "is_default": True}
    client.post("/api/v1/contacts", json=second, params={"telegram_id": TELEGRAM_ID})

    contacts = client.get("/api/v1/contacts", params={"telegram_id": TELEGRAM_ID}).json()
    defaults = [c for c in contacts if c["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["first_name"] == "Maria"


def test_set_default_contact_endpoint(client):
    first = client.post(
        "/api/v1/contacts", json=CONTACT_PAYLOAD, params={"telegram_id": TELEGRAM_ID}
    ).json()
    client.post(
        "/api/v1/contacts",
        json={**CONTACT_PAYLOAD, "first_name": "Maria", "is_default": True},
        params={"telegram_id": TELEGRAM_ID},
    )

    restored = client.post(f"/api/v1/contacts/{first['id']}/default").json()
    assert restored["is_default"] is True

    contacts = client.get("/api/v1/contacts", params={"telegram_id": TELEGRAM_ID}).json()
    assert sum(1 for c in contacts if c["is_default"]) == 1


def test_contacts_for_an_unknown_user_are_empty_not_an_error(client):
    assert client.get("/api/v1/contacts", params={"telegram_id": 1}).json() == []


# -------------------------------------------------------------------- orders


def test_create_order_freezes_the_price(client, fake_dynadot):
    response = client.post(
        "/api/v1/orders",
        json={"domain_name": "buyme.com", "years": 1},
        params={"telegram_id": TELEGRAM_ID},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["domain_name"] == "buyme.com"
    assert body["tld"] == "com"
    assert body["status"] == OrderStatus.DRAFT.value
    # Stored cost 0 + no override would be unsellable, so the fake quotes a price.
    assert body["price_rub"] > 0


def test_create_order_rejects_a_taken_domain_with_409(client, fake_dynadot):
    fake_dynadot.available = False
    response = client.post(
        "/api/v1/orders",
        json={"domain_name": "taken.com", "years": 1},
        params={"telegram_id": TELEGRAM_ID},
    )
    assert response.status_code == 409


def test_create_order_rejects_a_name_without_a_zone(client):
    response = client.post(
        "/api/v1/orders",
        json={"domain_name": "nozone", "years": 1},
        params={"telegram_id": TELEGRAM_ID},
    )
    assert response.status_code == 422


def test_creating_the_same_order_twice_reuses_the_draft(client):
    first = client.post(
        "/api/v1/orders",
        json={"domain_name": "double.com", "years": 1},
        params={"telegram_id": TELEGRAM_ID},
    ).json()
    second = client.post(
        "/api/v1/orders",
        json={"domain_name": "double.com", "years": 1},
        params={"telegram_id": TELEGRAM_ID},
    ).json()
    assert first["id"] == second["id"]


def test_order_attaches_the_default_contact_automatically(client):
    client.post(
        "/api/v1/contacts", json=CONTACT_PAYLOAD, params={"telegram_id": TELEGRAM_ID}
    )
    order = client.post(
        "/api/v1/orders",
        json={"domain_name": "withcontact.com", "years": 1},
        params={"telegram_id": TELEGRAM_ID},
    ).json()

    async def check():
        async with _session_factory()() as session:
            row = await session.get(Order, order["id"])
            return row.contact_profile_id

    assert run(check()) is not None


def test_create_order_for_unknown_user_is_404(client):
    response = client.post(
        "/api/v1/orders",
        json={"domain_name": "ghost.com", "years": 1},
        params={"telegram_id": 42},
    )
    assert response.status_code == 404


def test_list_and_get_orders(client):
    created = client.post(
        "/api/v1/orders",
        json={"domain_name": "listed.com", "years": 2},
        params={"telegram_id": TELEGRAM_ID},
    ).json()

    listed = client.get("/api/v1/orders", params={"telegram_id": TELEGRAM_ID}).json()
    assert [o["id"] for o in listed] == [created["id"]]

    fetched = client.get(f"/api/v1/orders/{created['id']}").json()
    assert fetched["years"] == 2


def test_cancel_an_unpaid_order(client):
    created = client.post(
        "/api/v1/orders",
        json={"domain_name": "cancelme.com", "years": 1},
        params={"telegram_id": TELEGRAM_ID},
    ).json()

    cancelled = client.post(f"/api/v1/orders/{created['id']}/cancel").json()
    assert cancelled["status"] == OrderStatus.CANCELLED.value


def test_cancelling_a_paid_order_is_rejected(client):
    """Self-service cancellation of paid money is the refund team's call."""
    created = client.post(
        "/api/v1/orders",
        json={"domain_name": "paid.com", "years": 1},
        params={"telegram_id": TELEGRAM_ID},
    ).json()

    async def mark_paid():
        async with _session_factory()() as session:
            order = await session.get(Order, created["id"])
            order.status = OrderStatus.PAID
            await session.commit()

    run(mark_paid())
    assert client.post(f"/api/v1/orders/{created['id']}/cancel").status_code == 409


# ------------------------------------------------------------------- domains


def test_zones_endpoint_falls_back_to_defaults(client):
    zones = client.get("/api/v1/domains/zones").json()
    assert "com" in zones


def test_zones_endpoint_reflects_admin_configuration(client):
    async def seed():
        async with _session_factory()() as session:
            session.add_all(
                [
                    TldPricing(tld="io", sort_order=1, is_enabled=True),
                    TldPricing(tld="shop", sort_order=2, is_enabled=False),
                ]
            )
            await session.commit()

    run(seed())
    assert client.get("/api/v1/domains/zones").json() == ["io"]


def test_ai_suggest_domains_returns_fallback_or_suggestions(client):
    res = client.post("/api/v1/domains/ai-suggest", json={"query": "testbrand"})
    assert res.status_code == 200
    data = res.json()
    assert "query" in data
    assert "suggestions" in data
    assert isinstance(data["suggestions"], list)


def test_my_domains_lists_only_the_users_domains(client):
    async def seed():
        async with _session_factory()() as session:
            from sqlalchemy import select

            user = await session.scalar(
                select(User).where(User.telegram_id == TELEGRAM_ID)
            )
            session.add(
                Domain(
                    user_id=user.id,
                    name="owned.com",
                    tld="com",
                    status=DomainStatus.ACTIVE,
                )
            )
            session.add(Domain(name="someone-else.com", tld="com"))
            await session.commit()

    run(seed())
    domains = client.get(
        "/api/v1/domains/mine", params={"telegram_id": TELEGRAM_ID}
    ).json()
    assert [d["name"] for d in domains] == ["owned.com"]


def test_nameserver_update_calls_the_registrar_and_persists(client, fake_dynadot):
    async def seed():
        async with _session_factory()() as session:
            domain = Domain(name="ns.com", tld="com", status=DomainStatus.ACTIVE)
            session.add(domain)
            await session.commit()
            return domain.id

    domain_id = run(seed())
    response = client.put(
        f"/api/v1/domains/{domain_id}/nameservers",
        json={"nameservers": ["ada.ns.cloudflare.com", "bob.ns.cloudflare.com"]},
    )

    assert response.status_code == 200
    assert response.json()["nameservers"] == (
        "ada.ns.cloudflare.com,bob.ns.cloudflare.com"
    )
    assert fake_dynadot.ns_calls


def test_nameserver_update_on_unknown_domain_is_404(client):
    response = client.put(
        "/api/v1/domains/does-not-exist/nameservers",
        json={"nameservers": ["a.ns.com"]},
    )
    assert response.status_code == 404


def test_nameserver_update_rejects_an_empty_list_at_the_schema(client):
    response = client.put(
        "/api/v1/domains/whatever/nameservers", json={"nameservers": []}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------- admin


def test_admin_stats_counts_orders_and_revenue(client):
    async def seed():
        async with _session_factory()() as session:
            from sqlalchemy import select

            user = await session.scalar(
                select(User).where(User.telegram_id == TELEGRAM_ID)
            )
            session.add_all(
                [
                    Order(
                        user_id=user.id,
                        domain_name="done.com",
                        tld="com",
                        status=OrderStatus.COMPLETED,
                        price_rub=Decimal("1000"),
                    ),
                    Order(
                        user_id=user.id,
                        domain_name="owed.com",
                        tld="com",
                        status=OrderStatus.REFUND_REQUIRED,
                        price_rub=Decimal("2000"),
                    ),
                ]
            )
            await session.commit()

    run(seed())
    stats = client.get("/api/v1/admin/stats").json()

    assert stats["users"] == 1
    assert stats["orders_total"] == 2
    assert stats["orders_completed"] == 1
    assert stats["refunds_pending"] == 1
    # Refund-pending money is not revenue; counting it overstates the books.
    assert stats["revenue_rub"] == 1000.0


def test_admin_refund_queue_lists_only_money_we_owe(client):
    async def seed():
        async with _session_factory()() as session:
            from sqlalchemy import select

            user = await session.scalar(
                select(User).where(User.telegram_id == TELEGRAM_ID)
            )
            session.add_all(
                [
                    Order(
                        user_id=user.id,
                        domain_name="refund.com",
                        tld="com",
                        status=OrderStatus.REFUND_REQUIRED,
                        price_rub=Decimal("1500"),
                        error_message="domain taken",
                    ),
                    Order(
                        user_id=user.id,
                        domain_name="fine.com",
                        tld="com",
                        status=OrderStatus.COMPLETED,
                        price_rub=Decimal("1500"),
                    ),
                ]
            )
            await session.commit()

    run(seed())
    refunds = client.get("/api/v1/admin/refunds").json()
    assert [o["domain_name"] for o in refunds] == ["refund.com"]

    resolved = client.post(
        f"/api/v1/admin/refunds/{refunds[0]['id']}/resolve",
        json={"note": "paid out manually"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == OrderStatus.REFUNDED.value
    assert client.get("/api/v1/admin/refunds").json() == []


def test_resolving_a_non_refund_order_is_rejected(client):
    async def seed():
        async with _session_factory()() as session:
            from sqlalchemy import select

            user = await session.scalar(
                select(User).where(User.telegram_id == TELEGRAM_ID)
            )
            order = Order(
                user_id=user.id,
                domain_name="notrefund.com",
                tld="com",
                status=OrderStatus.COMPLETED,
                price_rub=Decimal("100"),
            )
            session.add(order)
            await session.commit()
            return order.id

    order_id = run(seed())
    response = client.post(
        f"/api/v1/admin/refunds/{order_id}/resolve", json={"note": "oops"}
    )
    assert response.status_code == 409


def test_admin_orders_can_be_filtered_by_status(client):
    client.post(
        "/api/v1/orders",
        json={"domain_name": "draft.com", "years": 1},
        params={"telegram_id": TELEGRAM_ID},
    )

    all_orders = client.get("/api/v1/admin/orders").json()
    assert len(all_orders) == 1

    filtered = client.get(
        "/api/v1/admin/orders", params={"status": OrderStatus.COMPLETED.value}
    ).json()
    assert filtered == []


def test_admin_task_list_and_retry(client):
    from app.models.audit import DynadotTask

    async def seed():
        async with _session_factory()() as session:
            from sqlalchemy import select

            user = await session.scalar(
                select(User).where(User.telegram_id == TELEGRAM_ID)
            )
            order = Order(
                user_id=user.id,
                domain_name="retry-me.com",
                tld="com",
                status=OrderStatus.FAILED,
                price_rub=Decimal("900"),
            )
            session.add(order)
            await session.flush()
            task = DynadotTask(
                operation="register_domain",
                request_id="req-retry-1",
                order_id=order.id,
                status=TaskStatus.DEAD_LETTER,
                attempts=4,
                max_attempts=4,
                last_error="registrar down",
            )
            session.add(task)
            await session.commit()
            return task.id, order.id

    task_id, order_id = run(seed())

    tasks = client.get("/api/v1/admin/tasks").json()
    assert [t["id"] for t in tasks] == [task_id]

    dead = client.get(
        "/api/v1/admin/tasks", params={"status": TaskStatus.DEAD_LETTER.value}
    ).json()
    assert len(dead) == 1

    retried = client.post(f"/api/v1/admin/tasks/{task_id}/retry").json()
    assert retried["status"] == TaskStatus.QUEUED.value
    # Budget extended, history preserved.
    assert retried["attempts"] == 4
    assert retried["max_attempts"] == 7

    async def check_order():
        async with _session_factory()() as session:
            order = await session.get(Order, order_id)
            return order.status

    # The order is pulled back to PAID so the worker's claim can succeed.
    assert run(check_order()) is OrderStatus.PAID


def test_retrying_a_queued_task_is_rejected(client):
    from app.models.audit import DynadotTask

    async def seed():
        async with _session_factory()() as session:
            task = DynadotTask(
                operation="register_domain",
                request_id="req-queued-1",
                status=TaskStatus.QUEUED,
            )
            session.add(task)
            await session.commit()
            return task.id

    task_id = run(seed())
    assert client.post(f"/api/v1/admin/tasks/{task_id}/retry").status_code == 409


def test_retry_of_an_unknown_task_is_404(client):
    assert client.post("/api/v1/admin/tasks/nope/retry").status_code == 404


def test_admin_auth_protects_routes_when_configured(client, monkeypatch):
    import app.api.deps as deps_module
    from pydantic import SecretStr

    # Configure admin credentials
    current_settings = deps_module.get_settings()
    monkeypatch.setattr(current_settings, "admin_username", "admin")
    monkeypatch.setattr(current_settings, "admin_password", SecretStr("secret123"))

    # Unauthenticated -> 401
    resp = client.get("/api/v1/admin/stats")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Admin credentials required"

    # Wrong credentials -> 401
    resp = client.get("/api/v1/admin/stats", auth=("admin", "wrong"))
    assert resp.status_code == 401

    # Correct credentials -> 200
    resp = client.get("/api/v1/admin/stats", auth=("admin", "secret123"))
    assert resp.status_code == 200


def test_admin_login_endpoint(client, monkeypatch):
    import app.api.deps as deps_module
    from pydantic import SecretStr

    current_settings = deps_module.get_settings()
    monkeypatch.setattr(current_settings, "admin_username", "admin")
    monkeypatch.setattr(current_settings, "admin_password", SecretStr("secret123"))

    # Invalid login -> 401
    resp = client.post("/api/v1/admin/login", json={"username": "admin", "password": "wrongpassword"})
    assert resp.status_code == 401

    # Valid login -> 200
    resp = client.post("/api/v1/admin/login", json={"username": "admin", "password": "secret123"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "token" in data


def test_admin_settings_get_and_patch(client):
    # GET settings
    resp = client.get("/api/v1/admin/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "usd_to_rub_rate" in data
    assert "default_markup_percent" in data

    # PATCH settings
    patch_resp = client.patch(
        "/api/v1/admin/settings",
        json={"usd_to_rub_rate": 99.5, "default_markup_percent": 30.0},
    )
    assert patch_resp.status_code == 200
    updated = patch_resp.json()
    assert updated["usd_to_rub_rate"] == 99.5
    assert updated["default_markup_percent"] == 30.0


def test_admin_test_connection_endpoint(client):
    # Unconfigured telegram returns ok=False
    resp = client.post("/api/v1/admin/test-connection/telegram")
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "telegram"
    assert "ok" in data
    assert "message" in data
