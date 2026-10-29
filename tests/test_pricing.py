"""Tests for PricingService.

Pricing is a billing surface: search, checkout and the admin panel must agree
on the number to the kopeck, and an admin override must win over the global
default in a predictable order.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.models import Base
from app.models.domain import TldPricing
from app.services.pricing import PricingService, split_domain


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def settings():
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        default_markup_percent=25.0,
        usd_to_rub_rate=100.0,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("example.com", ("example", "com")),
        ("Example.COM", ("example", "com")),
        (".example.io", ("example", "io")),
        ("sub.example.dev", ("sub.example", "dev")),
        ("noext", ("", "")),
    ],
)
def test_split_domain(value, expected):
    assert split_domain(value) == expected


@pytest.mark.asyncio
async def test_global_markup_applies_without_an_override(session, settings):
    service = PricingService(session, settings)

    quote = await service.quote(tld="com", cost_usd=10.0)

    # 10 USD * 1.25 markup * 100 RUB/USD
    assert quote.price_rub == 1250.0
    assert quote.markup_percent == 25.0
    assert quote.fx_rate == 100.0
    assert quote.is_fixed is False


@pytest.mark.asyncio
async def test_years_multiply_both_cost_and_price(session, settings):
    service = PricingService(session, settings)

    quote = await service.quote(tld="com", cost_usd=10.0, years=3)

    assert quote.cost_usd == 30.0
    assert quote.price_rub == 3750.0


@pytest.mark.asyncio
async def test_per_zone_markup_overrides_the_global_default(session, settings):
    session.add(
        TldPricing(
            tld="io", register_cost_usd=Decimal("30"), markup_percent=Decimal("50")
        )
    )
    await session.commit()
    service = PricingService(session, settings)

    quote = await service.quote(tld="io", cost_usd=30.0)

    assert quote.markup_percent == 50.0
    assert quote.price_rub == 4500.0  # 30 * 1.5 * 100


@pytest.mark.asyncio
async def test_fixed_price_wins_over_every_markup(session, settings):
    """A hard price override is how an operator runs a promo — it must win."""
    session.add(
        TldPricing(
            tld="ru",
            register_cost_usd=Decimal("5"),
            markup_percent=Decimal("200"),
            fixed_price_rub=Decimal("299"),
        )
    )
    await session.commit()
    service = PricingService(session, settings)

    quote = await service.quote(tld="ru", cost_usd=5.0)

    assert quote.price_rub == 299.0
    assert quote.is_fixed is True
    assert quote.markup_percent == 0.0


@pytest.mark.asyncio
async def test_fixed_price_scales_with_years(session, settings):
    session.add(TldPricing(tld="ru", fixed_price_rub=Decimal("299")))
    await session.commit()
    service = PricingService(session, settings)

    quote = await service.quote(tld="ru", cost_usd=5.0, years=2)

    assert quote.price_rub == 598.0


@pytest.mark.asyncio
async def test_stored_cost_backfills_a_missing_live_quote(session, settings):
    """An unpriced search result must not become a free domain."""
    session.add(TldPricing(tld="dev", register_cost_usd=Decimal("12")))
    await session.commit()
    service = PricingService(session, settings)

    quote = await service.quote(tld="dev", cost_usd=0.0)

    assert quote.cost_usd == 12.0
    assert quote.price_rub == 1500.0


@pytest.mark.asyncio
async def test_zero_cost_with_no_override_yields_zero(session, settings):
    """Callers must detect this and refuse to sell, rather than charge 0."""
    service = PricingService(session, settings)
    quote = await service.quote(tld="xyz", cost_usd=0.0)
    assert quote.price_rub == 0.0


@pytest.mark.asyncio
async def test_tld_lookup_is_case_and_dot_insensitive(session, settings):
    session.add(TldPricing(tld="io", fixed_price_rub=Decimal("111")))
    await session.commit()
    service = PricingService(session, settings)

    assert (await service.quote(tld=".IO", cost_usd=1)).price_rub == 111.0


@pytest.mark.asyncio
async def test_enabled_tlds_falls_back_when_nothing_is_configured(session, settings):
    service = PricingService(session, settings)
    assert await service.enabled_tlds(["com", "net"]) == ["com", "net"]


@pytest.mark.asyncio
async def test_enabled_tlds_respects_sort_order_and_the_enabled_flag(
    session, settings
):
    session.add_all(
        [
            TldPricing(tld="org", sort_order=2, is_enabled=True),
            TldPricing(tld="com", sort_order=1, is_enabled=True),
            TldPricing(tld="xxx", sort_order=0, is_enabled=False),
        ]
    )
    await session.commit()
    service = PricingService(session, settings)

    assert await service.enabled_tlds(["fallback"]) == ["com", "org"]
