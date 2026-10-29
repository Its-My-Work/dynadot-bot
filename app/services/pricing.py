"""Price calculation: wholesale USD cost -> customer-facing RUB price.

Pricing lives in one place because three callers need identical numbers: the
search grid, order creation (which freezes the price) and the admin panel. A
divergence between them is a billing bug.

Resolution order for a zone:

1. ``TldPricing.fixed_price_rub`` — hard override, wins over everything.
2. ``TldPricing.markup_percent`` — per-zone markup.
3. ``Settings.default_markup_percent`` — global fallback.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.domain import TldPricing


@dataclass(frozen=True, slots=True)
class Quote:
    """A fully resolved price for one domain-year purchase."""

    tld: str
    cost_usd: float
    price_rub: float
    markup_percent: float
    fx_rate: float
    is_fixed: bool = False


def split_domain(domain: str) -> tuple[str, str]:
    """Split ``example.co.uk`` into ``("example", "co.uk")``.

    Multi-label suffixes are not inferred from a public-suffix list: Dynadot
    returns the zone it quoted, and guessing here would misprice. The last
    label is used, which is correct for every zone we sell.
    """
    base, _, tld = domain.strip().lower().lstrip(".").rpartition(".")
    if not base:
        # No dot at all: there is no zone to price. Returning the bare label as
        # a TLD would let `rpartition` fake a valid split and misprice it.
        return "", ""
    return base, tld


class PricingService:
    """Resolves customer prices, consulting admin overrides when present."""

    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    async def get_override(self, tld: str) -> TldPricing | None:
        return await self._session.scalar(
            select(TldPricing).where(TldPricing.tld == tld.lstrip(".").lower())
        )

    async def quote(self, *, tld: str, cost_usd: float, years: int = 1) -> Quote:
        """Price ``years`` of registration in RUB.

        ``cost_usd`` is the live Dynadot quote when we have one; the stored
        ``register_cost_usd`` is used as a fallback so an unpriced search result
        still gets a sane number instead of zero.
        """
        tld = tld.lstrip(".").lower()
        override = await self.get_override(tld)
        fx = float(self._settings.usd_to_rub_rate)

        if override is not None and not cost_usd:
            cost_usd = float(override.register_cost_usd or 0)

        if override is not None and override.fixed_price_rub is not None:
            price = float(override.fixed_price_rub) * years
            return Quote(
                tld=tld,
                cost_usd=round(cost_usd * years, 2),
                price_rub=round(price, 2),
                markup_percent=0.0,
                fx_rate=fx,
                is_fixed=True,
            )

        markup = (
            float(override.markup_percent)
            if override is not None and override.markup_percent is not None
            else float(self._settings.default_markup_percent)
        )
        total_cost = cost_usd * years
        price = total_cost * (1 + markup / 100) * fx
        return Quote(
            tld=tld,
            cost_usd=round(total_cost, 2),
            price_rub=round(price, 2),
            markup_percent=markup,
            fx_rate=fx,
        )

    async def enabled_tlds(self, fallback: list[str]) -> list[str]:
        """Zones offered in the search grid.

        Falls back to the hard-coded list while the admin has not configured
        any zone yet, so a fresh install still shows something useful.
        """
        rows = list(
            await self._session.scalars(
                select(TldPricing)
                .where(TldPricing.is_enabled.is_(True))
                .order_by(TldPricing.sort_order, TldPricing.tld)
            )
        )
        return [row.tld for row in rows] or fallback
