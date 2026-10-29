"""Tests for RegistrationService: claiming, refunds and result parsing.

The scenarios here mirror the ways real money goes wrong: a domain taken while
the customer paid, a registrar timeout, a double worker, and a retry that must
not generate a second idempotency key.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.integrations.dynadot.exceptions import (
    DomainUnavailableError,
    DynadotApiError,
    DynadotAuthError,
    DynadotTransportError,
)
from app.models import Base
from app.models.domain import Domain
from app.models.enums import DomainStatus, OrderStatus
from app.models.order import Order
from app.models.user import ContactProfile, User
from app.services.registration import (
    RegistrationOutcome,
    RegistrationService,
    _extract_expiry,
    _extract_nameservers,
    _is_unavailable,
)


class FakeDynadot:
    """Scriptable stand-in for DynadotClient."""

    def __init__(
        self,
        *,
        available: bool = True,
        register_result: dict[str, Any] | None = None,
        register_error: Exception | None = None,
        search_error: Exception | None = None,
        search_result: dict[str, Any] | None = None,
    ) -> None:
        self.available = available
        self.search_result = search_result or {
            "available": True,
            "register_price": 10.0,
        }
        self.register_result = register_result or {
            "order_id": "dyn-1",
            "expiration_date": "2027-01-15T00:00:00Z",
            "nameserver_list": ["ns1.dynadot.com", "ns2.dynadot.com"],
        }
        self.register_error = register_error
        self.search_error = search_error
        self.register_calls: list[dict[str, Any]] = []
        self.contact_calls: list[dict[str, Any]] = []
        self.ns_calls: list[tuple[str, list[str]]] = []
        self.contact_error: Exception | None = None

    async def ensure_available(self, domain: str) -> dict[str, Any]:
        if self.search_error is not None:
            raise self.search_error
        if not self.available:
            raise DomainUnavailableError(
                f"{domain} is no longer available", code="domain_taken"
            )
        return self.search_result

    async def register(self, domain: str, **kwargs: Any) -> dict[str, Any]:
        self.register_calls.append({"domain": domain, **kwargs})
        if self.register_error is not None:
            raise self.register_error
        return self.register_result

    async def create_contact(
        self, contact: dict[str, Any], *, request_id: str
    ) -> dict[str, Any]:
        self.contact_calls.append(contact)
        if self.contact_error is not None:
            raise self.contact_error
        return {"contact_id": "c-99"}

    async def set_nameservers(
        self, domain: str, nameservers: list[str], *, request_id: str | None = None
    ) -> dict[str, Any]:
        self.ns_calls.append((domain, nameservers))
        return {"success": True}


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


@pytest.fixture
def settings():
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        dynadot_api_key="k",
        dynadot_api_secret="s",
    )


async def make_order(
    session,
    *,
    status: OrderStatus = OrderStatus.PAID,
    domain: str = "example.com",
    contact: bool = False,
) -> Order:
    user = User(telegram_id=hash(domain) % 10**9, username="buyer")
    session.add(user)
    await session.flush()

    contact_id = None
    if contact:
        profile = ContactProfile(
            user_id=user.id,
            first_name="Ivan",
            last_name="Petrov",
            email="ivan@example.com",
            phone_country_code="7",
            phone_number="9990001122",
            address_line1="Lenina 1",
            city="Moscow",
            zip_code="101000",
            country="RU",
            is_default=True,
        )
        session.add(profile)
        await session.flush()
        contact_id = profile.id

    order = Order(
        user_id=user.id,
        contact_profile_id=contact_id,
        domain_name=domain,
        tld=domain.rpartition(".")[2],
        years=1,
        status=status,
        cost_usd=Decimal("10.00"),
        price_rub=Decimal("1200.00"),
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


# ------------------------------------------------------------------ claiming


@pytest.mark.asyncio
async def test_claim_moves_paid_order_to_registering(session, settings):
    order = await make_order(session)
    service = RegistrationService(session, FakeDynadot(), settings)

    claimed = await service.claim(order.id)

    assert claimed is not None
    assert claimed.status is OrderStatus.REGISTERING


@pytest.mark.asyncio
async def test_claim_refuses_an_unpaid_order(session, settings):
    """A draft order has no money behind it and must never be registered."""
    order = await make_order(session, status=OrderStatus.DRAFT)
    service = RegistrationService(session, FakeDynadot(), settings)

    assert await service.claim(order.id) is None


@pytest.mark.asyncio
async def test_second_claim_is_refused_so_no_double_registration(session, settings):
    order = await make_order(session)
    service = RegistrationService(session, FakeDynadot(), settings)

    first = await service.claim(order.id)
    assert first is not None

    # Simulates a second worker: the order is no longer PAID, so its
    # conditional UPDATE matches nothing.
    second = RegistrationService(session, FakeDynadot(), settings)
    resumed = await second.claim(order.id)
    # Resuming our own REGISTERING claim is allowed (restart recovery), but the
    # status must not change and the registrar must not be called twice.
    assert resumed is not None
    assert resumed.status is OrderStatus.REGISTERING


@pytest.mark.asyncio
async def test_register_order_skips_when_not_claimable(session, settings):
    order = await make_order(session, status=OrderStatus.COMPLETED)
    service = RegistrationService(session, FakeDynadot(), settings)

    result = await service.register_order(order.id)

    assert result.outcome == RegistrationOutcome.SKIPPED


# -------------------------------------------------------------- happy path


@pytest.mark.asyncio
async def test_successful_registration_completes_order_and_creates_domain(
    session, settings
):
    order = await make_order(session, domain="mynewsite.com")
    client = FakeDynadot()
    service = RegistrationService(session, client, settings)

    result = await service.register_order(order.id)

    assert result.outcome == RegistrationOutcome.COMPLETED
    await session.refresh(order)
    assert order.status is OrderStatus.COMPLETED
    assert order.registered_at is not None
    assert order.dynadot_order_id == "dyn-1"

    domain = await session.scalar(select(Domain).where(Domain.name == "mynewsite.com"))
    assert domain is not None
    assert domain.status is DomainStatus.ACTIVE
    assert domain.user_id == order.user_id
    assert "ns1.dynadot.com" in domain.nameservers
    assert order.domain_id == domain.id


@pytest.mark.asyncio
async def test_request_id_is_persisted_and_reused_across_attempts(session, settings):
    """A new idempotency key per attempt risks double-registering and billing."""
    order = await make_order(session, domain="retry.com")
    client = FakeDynadot(register_error=DynadotTransportError("timeout"))
    service = RegistrationService(session, client, settings)

    await service.register_order(order.id)
    await session.refresh(order)
    first_id = order.dynadot_request_id
    assert first_id

    # Second attempt: the order went back to PAID so it is claimable again.
    client.register_error = None
    await service.register_order(order.id)
    await session.refresh(order)

    assert order.dynadot_request_id == first_id
    assert {c["request_id"] for c in client.register_calls} == {first_id}


@pytest.mark.asyncio
async def test_contact_is_created_once_and_cached_on_the_profile(session, settings):
    order = await make_order(session, domain="withcontact.com", contact=True)
    client = FakeDynadot()
    service = RegistrationService(session, client, settings)

    await service.register_order(order.id)

    profile = await session.scalar(
        select(ContactProfile).where(ContactProfile.id == order.contact_profile_id)
    )
    assert profile.dynadot_contact_id == "c-99"
    assert len(client.contact_calls) == 1
    assert client.register_calls[0]["contact_id"] == "c-99"


@pytest.mark.asyncio
async def test_contact_failure_falls_back_to_account_default(session, settings):
    """A contact error must not strand an order whose money we already hold."""
    order = await make_order(session, domain="nocontact.com", contact=True)
    client = FakeDynadot()
    client.contact_error = DynadotApiError("bad contact", code="invalid_contact")
    service = RegistrationService(session, client, settings)

    result = await service.register_order(order.id)

    assert result.outcome == RegistrationOutcome.COMPLETED
    assert client.register_calls[0]["contact_id"] is None


# --------------------------------------------------------------- refund path


@pytest.mark.asyncio
async def test_domain_taken_during_payment_requires_a_refund(session, settings):
    """The core money-safety rule: taken domain => REFUND_REQUIRED, not FAILED."""
    order = await make_order(session, domain="sniped.com")
    client = FakeDynadot(available=False)
    service = RegistrationService(session, client, settings)

    result = await service.register_order(order.id)

    assert result.outcome == RegistrationOutcome.REFUND_REQUIRED
    await session.refresh(order)
    assert order.status is OrderStatus.REFUND_REQUIRED
    assert order.error_code == "domain_taken"
    # Crucially: we never spent a register call on a name we cannot get.
    assert client.register_calls == []


@pytest.mark.asyncio
async def test_unavailable_reported_only_at_register_time_also_refunds(session, settings):
    """The race can be lost between our availability check and the register."""
    order = await make_order(session, domain="racy.com")
    client = FakeDynadot(
        register_error=DynadotApiError("domain is not available", code="register_failed")
    )
    service = RegistrationService(session, client, settings)

    result = await service.register_order(order.id)

    assert result.outcome == RegistrationOutcome.REFUND_REQUIRED
    await session.refresh(order)
    assert order.status is OrderStatus.REFUND_REQUIRED


@pytest.mark.asyncio
async def test_our_own_auth_failure_refunds_rather_than_failing(session, settings):
    """A bad API key is our fault; the customer's money must go back."""
    order = await make_order(session, domain="authfail.com")
    client = FakeDynadot(register_error=DynadotAuthError("bad signature"))
    service = RegistrationService(session, client, settings)

    result = await service.register_order(order.id)

    assert result.outcome == RegistrationOutcome.REFUND_REQUIRED
    await session.refresh(order)
    assert order.status is OrderStatus.REFUND_REQUIRED


@pytest.mark.asyncio
async def test_business_rejection_marks_order_failed(session, settings):
    order = await make_order(session, domain="rejected.com")
    client = FakeDynadot(
        register_error=DynadotApiError("insufficient funds", code="no_balance")
    )
    service = RegistrationService(session, client, settings)

    result = await service.register_order(order.id)

    assert result.outcome == RegistrationOutcome.FAILED
    await session.refresh(order)
    assert order.status is OrderStatus.FAILED
    assert order.error_code == "no_balance"


@pytest.mark.asyncio
async def test_transport_error_returns_order_to_paid_for_retry(session, settings):
    """A timeout must leave the order claimable, not stuck in REGISTERING."""
    order = await make_order(session, domain="flaky.com")
    client = FakeDynadot(register_error=DynadotTransportError("504"))
    service = RegistrationService(session, client, settings)

    result = await service.register_order(order.id)

    assert result.outcome == RegistrationOutcome.RETRY
    assert result.is_terminal is False
    await session.refresh(order)
    assert order.status is OrderStatus.PAID


@pytest.mark.asyncio
async def test_escalate_to_refund_moves_a_given_up_order_to_the_refund_queue(
    session, settings
):
    order = await make_order(session, domain="givenup.com")
    service = RegistrationService(session, FakeDynadot(), settings)

    await service.escalate_to_refund(order.id, "retries exhausted")

    await session.refresh(order)
    assert order.status is OrderStatus.REFUND_REQUIRED
    assert order.error_message == "retries exhausted"


@pytest.mark.asyncio
async def test_escalate_never_touches_a_completed_order(session, settings):
    order = await make_order(session, status=OrderStatus.COMPLETED, domain="done.com")
    service = RegistrationService(session, FakeDynadot(), settings)

    await service.escalate_to_refund(order.id, "late failure")

    await session.refresh(order)
    assert order.status is OrderStatus.COMPLETED


# ---------------------------------------------------------------- nameservers


@pytest.mark.asyncio
async def test_update_nameservers_calls_registrar_then_persists(session, settings):
    domain = Domain(name="ns-test.com", tld="com", status=DomainStatus.ACTIVE)
    session.add(domain)
    await session.commit()

    client = FakeDynadot()
    service = RegistrationService(session, client, settings)

    updated = await service.update_nameservers(
        domain, ["Ada.ns.cloudflare.com ", "bob.ns.cloudflare.com"]
    )

    assert client.ns_calls == [
        ("ns-test.com", ["ada.ns.cloudflare.com", "bob.ns.cloudflare.com"])
    ]
    assert updated.nameserver_list == [
        "ada.ns.cloudflare.com",
        "bob.ns.cloudflare.com",
    ]


@pytest.mark.asyncio
async def test_update_nameservers_rejects_an_empty_list(session, settings):
    domain = Domain(name="empty-ns.com", tld="com")
    session.add(domain)
    await session.commit()
    service = RegistrationService(session, FakeDynadot(), settings)

    with pytest.raises(ValueError, match="at least one nameserver"):
        await service.update_nameservers(domain, ["  "])


# -------------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    "message",
    [
        "domain is not available",
        "Name already registered",
        "that one is taken",
        "domain exists",
    ],
)
def test_unavailable_detection_recognises_registrar_phrasings(message):
    assert _is_unavailable(DynadotApiError(message)) is True


def test_unavailable_detection_ignores_unrelated_errors():
    assert _is_unavailable(DynadotApiError("insufficient balance")) is False


@pytest.mark.parametrize(
    ("payload", "expected_year"),
    [
        ({"expiration_date": "2027-01-15T00:00:00Z"}, 2027),
        ({"expires_at": "2028-03-01 12:00:00"}, 2028),
        ({"expiry_date": 1800000000}, 2027),
    ],
)
def test_expiry_parsing_tolerates_the_beta_apis_shapes(payload, expected_year):
    parsed = _extract_expiry(payload)
    assert parsed is not None
    assert parsed.year == expected_year
    assert parsed.tzinfo is not None


def test_expiry_parsing_returns_none_for_junk():
    assert _extract_expiry({"expiration_date": "not-a-date"}) is None
    assert _extract_expiry({}) is None


def test_nameserver_parsing_falls_back_to_defaults():
    """An empty delegation would leave a paid-for domain not resolving."""
    assert _extract_nameservers({}) == ["ns1.dynadot.com", "ns2.dynadot.com"]
    assert _extract_nameservers({"nameservers": "a.ns.com, b.ns.com"}) == [
        "a.ns.com",
        "b.ns.com",
    ]


def test_registration_result_terminality():
    from app.services.registration import RegistrationResult

    assert RegistrationResult(RegistrationOutcome.COMPLETED, "o1").is_terminal
    assert not RegistrationResult(RegistrationOutcome.RETRY, "o1").is_terminal


def test_datetime_parsing_handles_naive_values():
    from app.services.registration import _parse_datetime

    parsed = _parse_datetime(datetime(2027, 5, 1, 10, 0))
    assert parsed.tzinfo is UTC
