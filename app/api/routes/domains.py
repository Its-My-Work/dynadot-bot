"""Domain search endpoints.

Scaffold: wiring, validation and i18n are real; pricing and persistence land
with the pricing service.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DbSession, Dynadot, Translate
from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.dynadot.exceptions import DynadotError
from app.models.domain import Domain
from app.models.user import User
from app.schemas import (
    CatalogTldItem,
    DomainRead,
    DomainSearchRequest,
    DomainSearchResponse,
    DomainSearchResult,
    NameserverUpdate,
)
from app.services.pricing import PricingService
from app.services.registration import RegistrationService

logger = get_logger(__name__)
router = APIRouter(prefix="/domains", tags=["domains"])

TLD_CATEGORIES: dict[str, list[str]] = {
    "all": [
        "com", "net", "org", "co", "io", "ai", "xyz", "pro", "store",
        "tech", "app", "cloud", "space", "site", "online", "fun", "icu",
        "website", "top", "shop", "biz", "info", "agency", "vip", "me",
        "life", "art", "design", "digital", "club",
    ],
    "popular": ["com", "net", "org", "co", "io", "ai", "xyz", "pro", "store"],
    "tech": ["ai", "io", "tech", "app", "cloud", "space", "digital"],
    "business": ["pro", "store", "shop", "biz", "co", "agency", "vip"],
    "budget": ["xyz", "store", "space", "site", "online", "fun", "icu", "website", "top"],
    "creative": ["me", "art", "design", "life", "club", "info"],
}

DEFAULT_TLDS = TLD_CATEGORIES["all"]


def _apply_markup(cost_usd: float) -> float:
    """Convert wholesale USD cost into the customer-facing RUB price."""
    marked_up = cost_usd * (1 + settings.default_markup_percent / 100)
    return round(marked_up * settings.usd_to_rub_rate, 2)


@router.post("/search", response_model=DomainSearchResponse)
async def search_domains(
    payload: DomainSearchRequest,
    dynadot: Dynadot,
    session: DbSession,
    t: Translate,
) -> DomainSearchResponse:
    """Check availability of a name across zones with category and pagination support."""
    pricing_svc = PricingService(session, settings)
    name = payload.query.strip().lower()

    if "." in name:
        base, _, tld = name.rpartition(".")
        if not base:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=t("search.invalid_name"),
            )
        candidates = [f"{base}.{tld}"]
        total_zones = 1
        has_more = False
    else:
        if payload.tlds:
            pool = [t_item.lstrip(".") for t_item in payload.tlds]
        elif payload.category == "budget":
            # Dynamically select all enabled TLDs where current retail price <= 500 RUB
            from app.models.domain import TldPricing
            tld_rows = list(
                await session.scalars(
                    select(TldPricing).where(TldPricing.is_enabled.is_(True))
                )
            )
            budget_pool = []
            for r in tld_rows:
                quote = await pricing_svc.quote(
                    tld=r.tld, cost_usd=float(r.register_cost_usd or 0.0), years=1
                )
                if quote.price_rub <= 500.0:
                    budget_pool.append(r.tld)
            pool = budget_pool if budget_pool else TLD_CATEGORIES["budget"]
        elif payload.category and payload.category in TLD_CATEGORIES:
            pool = TLD_CATEGORIES[payload.category]
        else:
            enabled = await pricing_svc.enabled_tlds(DEFAULT_TLDS)
            pool = enabled if enabled else DEFAULT_TLDS

        total_zones = len(pool)
        offset = max(0, payload.offset or 0)
        limit = min(30, max(5, payload.limit or 15))
        sliced_tlds = pool[offset : offset + limit]
        candidates = [f"{name}.{tld}" for tld in sliced_tlds]
        has_more = (offset + limit) < total_zones

    try:
        raw = await dynadot.bulk_search(candidates)
    except DynadotError as exc:
        logger.warning("domain search failed", extra={"error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=t("error.registrar_unavailable"),
        ) from exc

    items = _iter_search_items(raw, candidates)
    results = [await _to_result(item, t, pricing_svc) for item in items]

    alternatives: list[str] = []
    if "." not in name and (payload.offset or 0) == 0:
        com_result = next((r for r in results if r.tld == "com"), None)
        if com_result and not com_result.available:
            alternatives = [
                f"get{name}.com",
                f"{name}app.com",
                f"the{name}.com",
                f"{name}hub.com",
            ]

    return DomainSearchResponse(
        query=name,
        results=results,
        alternatives=alternatives,
        has_more=has_more,
        total_available_zones=total_zones,
    )


@router.get("/catalog", response_model=list[CatalogTldItem])
async def list_catalog(
    session: DbSession,
    dynadot: Dynadot,
) -> list[CatalogTldItem]:
    """Catalog of all zones with current retail prices for browsing."""
    from app.models.domain import TldPricing

    pricing_svc = PricingService(session, settings)
    rows = list(
        await session.scalars(
            select(TldPricing)
            .where(TldPricing.is_enabled.is_(True))
            .order_by(TldPricing.sort_order, TldPricing.tld)
        )
    )

    def get_cat(tld: str) -> str:
        for cat, list_tlds in TLD_CATEGORIES.items():
            if cat != "all" and tld in list_tlds:
                return cat
        return "other"

    # If DB only has initial seed (<= 35 zones), dynamically fetch full Dynadot TLD prices and augment!
    items: list[CatalogTldItem] = []
    seen_tlds: set[str] = set()

    for r in rows:
        seen_tlds.add(r.tld)
        quote = await pricing_svc.quote(
            tld=r.tld, cost_usd=float(r.register_cost_usd or 0.0), years=1
        )
        items.append(
            CatalogTldItem(
                tld=r.tld,
                price_rub=quote.price_rub,
                category=get_cat(r.tld),
                is_featured=r.is_featured,
            )
        )

    try:
        data = await dynadot.get_tld_prices(currency="USD")
        tld_price_list = data.get("tld_price_list") or []
        for item in tld_price_list:
            raw_tld = str(item.get("tld") or "").lstrip(".").lower()
            if not raw_tld or raw_tld in seen_tlds:
                continue
            years_p = item.get("all_years_register_price")
            cost = None
            if isinstance(years_p, list) and len(years_p) > 0:
                try:
                    cost = float(years_p[0])
                except (ValueError, TypeError):
                    pass
            if cost is None:
                raw_reg = item.get("register_price") or item.get("registration_price")
                try:
                    cost = float(raw_reg) if raw_reg is not None else None
                except (ValueError, TypeError):
                    pass
            if cost is not None:
                quote = await pricing_svc.quote(tld=raw_tld, cost_usd=cost, years=1)
                items.append(
                    CatalogTldItem(
                        tld=raw_tld,
                        price_rub=quote.price_rub,
                        category=get_cat(raw_tld),
                        is_featured=False,
                    )
                )
                seen_tlds.add(raw_tld)
    except Exception as exc:
        logger.warning("could not fetch live dynadot full catalog", extra={"error": str(exc)})

    # Sort featured first, then alphabetical by TLD
    items.sort(key=lambda x: (not x.is_featured, x.tld))
    return items


@router.get("/zones", response_model=list[str])
async def list_zones(session: DbSession) -> list[str]:
    """Zones offered in the TMA grid, admin-configurable."""
    pricing = PricingService(session, settings)
    return await pricing.enabled_tlds(DEFAULT_TLDS)


@router.get("/mine", response_model=list[DomainRead])
async def list_my_domains(
    telegram_id: int,
    session: DbSession,
) -> list[Domain]:
    """Domains the customer owns, for the TMA's "My domains" tab."""
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        return []
    result = await session.scalars(
        select(Domain).where(Domain.user_id == user.id).order_by(Domain.created_at.desc())
    )
    return list(result)


@router.put("/{domain_id}/nameservers", response_model=DomainRead)
async def update_nameservers(
    domain_id: str,
    payload: NameserverUpdate,
    session: DbSession,
    dynadot: Dynadot,
    t: Translate,
) -> Domain:
    """Delegate a domain to the customer's own nameservers (e.g. Cloudflare).

    The registrar call happens before the local write, so a rejected change is
    never reflected as applied in our UI.
    """
    domain = await session.scalar(select(Domain).where(Domain.id == domain_id))
    if domain is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )

    service = RegistrationService(session, dynadot, settings)
    try:
        return await service.update_nameservers(domain, payload.nameservers)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except DynadotError as exc:
        logger.warning(
            "nameserver update failed",
            extra={"domain": domain.name, "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=t("error.registrar_unavailable"),
        ) from exc


def _iter_search_items(raw: dict, candidates: list[str]) -> list[dict]:
    """Normalise Dynadot's loosely-specified bulk_search payload."""
    items = raw.get("domain_result_list") or raw.get("results") or raw.get("data")
    if isinstance(items, list):
        return [i for i in items if isinstance(i, dict)]
    # Fall back to "unknown" rows so the UI still renders every requested zone.
    return [{"domain_name": c, "available": None} for c in candidates]


async def _to_result(
    item: dict, t: Translate, pricing_svc: PricingService
) -> DomainSearchResult:
    domain = str(item.get("domain_name") or item.get("domain") or "")
    tld = domain.rpartition(".")[2].lower()

    available_raw = item.get("available")
    available = False
    if isinstance(available_raw, str):
        available = available_raw.strip().lower() in ("yes", "true", "1")
    elif isinstance(available_raw, bool):
        available = available_raw

    # Dynadot returns "premium": "no" or "yes" or bool
    prem_raw = item.get("premium") or item.get("is_premium")
    is_premium = False
    if isinstance(prem_raw, str):
        is_premium = prem_raw.strip().lower() in ("yes", "true", "1")
    elif isinstance(prem_raw, bool):
        is_premium = prem_raw

    # Extract wholesale registration price in USD
    cost_usd: float | None = None
    price_list = item.get("price_list")
    if isinstance(price_list, list) and len(price_list) > 0:
        p1 = next(
            (p for p in price_list if "1 year" in str(p.get("unit", "")).lower()),
            price_list[0],
        )
        reg_p = p1.get("registration_price") or p1.get("price")
        if reg_p is not None:
            try:
                cost_usd = float(reg_p)
            except (ValueError, TypeError):
                cost_usd = None

    if cost_usd is None:
        raw_p = item.get("price") or item.get("register_price")
        try:
            cost_usd = float(raw_p) if raw_p is not None else None
        except (ValueError, TypeError):
            cost_usd = None

    # Calculate resale price in RUB through PricingService
    quote = await pricing_svc.quote(tld=tld, cost_usd=cost_usd or 0.0, years=1)

    if available:
        price_rub = quote.price_rub
        price_usd = quote.cost_usd
        if price_rub <= 0:
            fallback_quote = await pricing_svc.quote(tld=tld, cost_usd=10.0, years=1)
            price_rub = fallback_quote.price_rub
            price_usd = fallback_quote.cost_usd
    else:
        price_rub = None
        price_usd = None

    msg = t("search.available") if available else t("search.taken")
    if available and is_premium:
        msg = f"{t('search.available')} · {t('search.premium')}"

    return DomainSearchResult(
        domain=domain,
        tld=tld,
        available=available,
        is_premium=is_premium,
        price_usd=price_usd,
        price_rub=price_rub,
        message=msg,
    )
