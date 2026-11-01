"""Domain search endpoints.

Scaffold: wiring, validation and i18n are real; pricing and persistence land
with the pricing service.
"""

from __future__ import annotations

import json
import os
import time

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbSession, Dynadot, Translate
from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.dynadot.exceptions import DynadotError
from app.models.audit import AppSetting
from app.models.domain import Domain, DomainRedirect, DomainWatchlist, TldPricing
from app.models.enums import OrderStatus
from app.models.order import Order
from app.models.user import User
from app.schemas import (
    AiSuggestRequest,
    AiSuggestResponse,
    CatalogTldItem,
    CategoriesResponse,
    CategoryItem,
    DnsRecordItem,
    DnsRecordsPayload,
    DomainDetailsResponse,
    DomainLockPayload,
    DomainPrivacyPayload,
    DomainQuickPresetPayload,
    DomainRead,
    DomainRedirectResponse,
    DomainRedirectUpdate,
    DomainRenewPayload,
    DomainRenewResponse,
    DomainSearchRequest,
    DomainSearchResponse,
    DomainSearchResult,
    DomainWatchlistRequest,
    DomainWatchlistResponse,
    NameserverUpdate,
)
from app.services.pricing import PricingService
from app.services.query_normalizer import normalize_domain_query
from app.services.registration import RegistrationService

logger = get_logger(__name__)
router = APIRouter(prefix="/domains", tags=["domains"])

_cached_all_tlds: list[str] = []
_cached_all_tlds_time: float = 0.0


async def _get_all_available_tlds(dynadot: Dynadot) -> list[str]:
    """Retrieve full list of 800+ TLDs supported by Dynadot with in-memory caching."""
    global _cached_all_tlds, _cached_all_tlds_time
    now = time.monotonic()
    if _cached_all_tlds and (now - _cached_all_tlds_time) < 3600:
        return _cached_all_tlds

    tlds = list(DEFAULT_TLDS)
    try:
        data = await dynadot.get_tld_prices(currency="USD")
        price_list = data.get("tld_price_list") or []
        extra_tlds = []
        for item in price_list:
            raw = str(item.get("tld") or "").lstrip(".").lower()
            if raw and raw not in tlds:
                extra_tlds.append(raw)
        if extra_tlds:
            tlds.extend(extra_tlds)
            _cached_all_tlds = tlds
            _cached_all_tlds_time = now
    except Exception as exc:
        logger.warning(
            "could not load full tld list for pagination", extra={"error": str(exc)}
        )

    return tlds


TLD_CATEGORIES: dict[str, list[str]] = {
    "all": [
        "com",
        "net",
        "org",
        "co",
        "io",
        "ai",
        "xyz",
        "pro",
        "store",
        "tech",
        "app",
        "cloud",
        "space",
        "site",
        "online",
        "fun",
        "icu",
        "website",
        "top",
        "shop",
        "biz",
        "info",
        "agency",
        "vip",
        "me",
        "life",
        "art",
        "design",
        "digital",
        "club",
    ],
    "popular": ["com", "net", "org", "co", "io", "ai", "xyz", "pro", "store"],
    "tech": ["ai", "io", "tech", "app", "cloud", "space", "digital"],
    "business": ["pro", "store", "shop", "biz", "co", "agency", "vip"],
    "budget": ["xyz", "store", "space", "site", "online", "fun", "icu", "website", "top"],
    "creative": ["me", "art", "design", "life", "club", "info"],
}

DEFAULT_TLDS = TLD_CATEGORIES["all"]

DEFAULT_CATEGORIES: list[dict] = [
    {
        "id": "budget",
        "name": "До 700 ₽",
        "name_en": "Under 700 ₽",
        "icon": "💰",
        "sort_order": 1,
        "is_enabled": True,
        "rule_type": "price_max",
        "rule_value": 700.0,
        "tlds": [
            "xyz",
            "store",
            "space",
            "site",
            "online",
            "fun",
            "icu",
            "website",
            "top",
        ],
    },
    {
        "id": "popular",
        "name": "Топ",
        "name_en": "Top",
        "icon": "🔥",
        "sort_order": 2,
        "is_enabled": True,
        "rule_type": "custom_tlds",
        "rule_value": None,
        "tlds": ["com", "net", "org", "co", "io", "ai", "xyz", "pro", "store"],
    },
    {
        "id": "tech",
        "name": "AI & IT",
        "name_en": "AI & IT",
        "icon": "🤖",
        "sort_order": 3,
        "is_enabled": True,
        "rule_type": "custom_tlds",
        "rule_value": None,
        "tlds": ["ai", "io", "tech", "app", "cloud", "space", "digital"],
    },
    {
        "id": "business",
        "name": "Бизнес",
        "name_en": "Business",
        "icon": "💼",
        "sort_order": 4,
        "is_enabled": True,
        "rule_type": "custom_tlds",
        "rule_value": None,
        "tlds": ["pro", "store", "shop", "biz", "co", "agency", "vip"],
    },
    {
        "id": "creative",
        "name": "Креатив",
        "name_en": "Creative",
        "icon": "🎨",
        "sort_order": 5,
        "is_enabled": True,
        "rule_type": "custom_tlds",
        "rule_value": None,
        "tlds": ["me", "art", "design", "life", "club", "info"],
    },
]


async def get_all_categories_config(session: AsyncSession) -> list[dict]:
    """Fetch categories from AppSetting or return defaults."""
    row = await session.get(AppSetting, "domain_categories")
    if row and row.value:
        try:
            items = json.loads(row.value)
            if isinstance(items, list) and items:
                return items
        except Exception as exc:
            logger.warning("failed to parse domain_categories", extra={"error": str(exc)})
    return DEFAULT_CATEGORIES


async def resolve_category_tlds(
    category_id: str,
    session: AsyncSession,
    pricing_svc: PricingService,
    dynadot: Dynadot,
) -> list[str]:
    """Resolve pool of TLDs for a category based on its dynamic rule."""
    categories = await get_all_categories_config(session)
    cat_map = {c["id"]: c for c in categories}
    cat = cat_map.get(category_id)
    if not cat:
        return TLD_CATEGORIES.get(category_id, DEFAULT_TLDS)

    rule_type = cat.get("rule_type", "custom_tlds")
    if rule_type == "price_max":
        max_price = float(cat.get("rule_value") or 700.0)
        tld_rows = list(
            await session.scalars(
                select(TldPricing).where(TldPricing.is_enabled.is_(True))
            )
        )
        pool = []
        for r in tld_rows:
            quote = await pricing_svc.quote(
                tld=r.tld, cost_usd=float(r.register_cost_usd or 0.0), years=1
            )
            if quote.price_rub <= max_price:
                pool.append(r.tld)
        return pool if pool else cat.get("tlds", [])
    elif rule_type == "featured":
        rows = list(
            await session.scalars(
                select(TldPricing).where(
                    TldPricing.is_enabled.is_(True),
                    TldPricing.is_featured.is_(True),
                )
            )
        )
        return [r.tld for r in rows] if rows else cat.get("tlds", [])
    elif rule_type == "all":
        return await _get_all_available_tlds(dynadot)
    else:  # custom_tlds
        return [t.lstrip(".") for t in cat.get("tlds", [])]


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
    raw_input = payload.query.strip().lower()
    norm_name, suggested_query = normalize_domain_query(raw_input)
    name = norm_name or raw_input

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
        elif payload.category and payload.category != "all":
            pool = await resolve_category_tlds(
                payload.category, session, pricing_svc, dynadot
            )
        else:
            # "all" category: paginate across all 800+ available zones!
            pool = await _get_all_available_tlds(dynadot)

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
        suggested_query=suggested_query,
        original_query=raw_input if (suggested_query or norm_name != raw_input) else None,
    )


_AI_USER_LOCKS: dict[str, float] = {}
_AI_MIN_INTERVAL_SECONDS = 1.0  # Cooldown between staged requests per client


@router.post("/ai-suggest", response_model=AiSuggestResponse)
async def ai_suggest_domains(
    payload: AiSuggestRequest,
    dynadot: Dynadot,
    session: DbSession,
    t: Translate,
    request: Request,
) -> AiSuggestResponse:
    """Generate smart creative brandable domain suggestions using AI and verify real-time availability."""
    # Prevent concurrent spam and flood per client
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    last_req = _AI_USER_LOCKS.get(client_ip, 0)
    if now - last_req < _AI_MIN_INTERVAL_SECONDS:
        # Clean expired keys
        if len(_AI_USER_LOCKS) > 1000:
            _AI_USER_LOCKS.clear()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком частые запросы к подбору. Подождите секунду.",
        )
    _AI_USER_LOCKS[client_ip] = now

    raw_query = payload.query.strip().lower()
    if not raw_query:
        return AiSuggestResponse(query="", suggestions=[])

    pricing_svc = PricingService(session, settings)
    api_key = (
        settings.omniroute_api_key.get_secret_value()
        if settings.omniroute_api_key
        else None
    )
    if not api_key:
        api_key = os.environ.get(
            "HERMES_CUSTOM_OMNIROUTE_ITSMYWORK_VIP_API_KEY"
        ) or os.environ.get("HERMES_CUSTOM_OMNIROUTE_MANAGE_API_KEY")

    suggested_domains: list[str] = []
    keywords: list[str] = []
    target_tlds = [t.lower().lstrip(".") for t in payload.tlds if t.strip()]
    if not target_tlds and payload.category and payload.category != "all":
        target_tlds = await resolve_category_tlds(
            payload.category, session, pricing_svc, dynadot
        )

    # If the client already passed pre-generated candidates for staged/chunked verification:
    if payload.candidates:
        unique_candidates = [c.strip().lower() for c in payload.candidates if "." in c][
            :25
        ]
        try:
            raw = await dynadot.bulk_search(unique_candidates)
        except Exception as exc:
            logger.warning(
                "bulk_search for staged candidates failed", extra={"error": str(exc)}
            )
            raw = {"domain_result_list": []}

        items = _iter_search_items(raw, unique_candidates)
        results = [await _to_result(item, t, pricing_svc) for item in items]
        available_only = [r for r in results if r.available and r.price_rub is not None]
        return AiSuggestResponse(
            query=raw_query,
            keywords=[],
            candidates=unique_candidates,
            suggestions=available_only,
            total_checked=len(unique_candidates),
        )

    tld_spec = (
        f"in these exact TLD zones ONLY: {', '.join('.' + t for t in target_tlds)}"
        if target_tlds
        else "in top commercial TLDs (.com, .co, .io, .pro, .store, .app, .tech, .online, .space)"
    )

    is_concept = (" " in raw_query) or (len(raw_query.split()) > 1)

    if is_concept:
        keywords = [w.strip() for w in raw_query.split() if len(w.strip()) > 1][:4]
    else:
        brand_base = raw_query.split(".")[0].replace(" ", "").strip()
        if brand_base:
            keywords = [brand_base]

    if api_key:
        try:
            if is_concept:
                prompt = (
                    f"You are an elite domain name strategist and startup branding agency. A user is looking for a domain for their business concept or keywords: '{raw_query}'.\n"
                    f"Extract the core business themes and keywords, and suggest 14 short, punchy, modern brandable domain names (under 12 chars per name, 2-3 syllables) {tld_spec}.\n"
                    f"Guidelines:\n"
                    f"1. Blend the keywords into memorable English/Latin startup brands (e.g. for 'кофе доставка спб' -> piterbrew.co, coffeerun.pro, nordiccup.io, spbcoffee.store; for 'ремонт квартир' -> flatmaker.pro, remontpro.co, homecraft.io).\n"
                    f"2. Never produce long clumsy domains like 'kofespbdostavka.com'. Make them short, professional, and brandable.\n"
                    f"3. Return ONLY a clean comma-separated list of domain names, nothing else."
                )
            else:
                brand_base = raw_query.split(".")[0].replace(" ", "").strip()
                prompt = (
                    f"You are an elite domain name branding expert. A user searched for the domain brand '{brand_base}'.\n"
                    f"Suggest 14 high-quality alternative domain names that preserve the user's original brand identity '{brand_base}' {tld_spec}.\n"
                    f"Guidelines:\n"
                    f"1. Every single domain MUST directly contain '{brand_base}' or its clear root.\n"
                    f"2. Add popular tech startup prefixes (get, the, try, use) or suffixes (app, hq, hub, lab, dev, tech, online) {tld_spec}.\n"
                    f"3. Examples: get{brand_base}.com, {brand_base}app.com, {brand_base}.io, {brand_base}.co, {brand_base}.pro, {brand_base}hq.com, {brand_base}.tech, try{brand_base}.com.\n"
                    f"4. If input is Russian, transliterate it first to Latin letters.\n"
                    f"5. NEVER suggest random words that discard the brand (e.g. NEVER suggest 'show.store' or 'deal.shop'). Keep the brand identity.\n"
                    f"Return ONLY a clean comma-separated list of domain names, nothing else."
                )
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.post(
                    f"{settings.omniroute_base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.omniroute_model or "fast",
                        "messages": [
                            {
                                "role": "system",
                                "content": "You are a domain name branding expert. Return ONLY a comma-separated list of domain names.",
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": 150,
                        "temperature": 0.35,
                    },
                )
                if res.status_code == 200:
                    text = res.json()["choices"][0]["message"]["content"]
                    for part in text.split(","):
                        cand = part.strip().strip("'\"` \n").lower()
                        if "." in cand and len(cand) < 60:
                            cand_tld = cand.split(".")[-1]
                            if target_tlds and cand_tld not in target_tlds:
                                continue
                            suggested_domains.append(cand)
        except Exception as exc:
            logger.warning("AI domain suggestion call failed", extra={"error": str(exc)})

    # Fallback if AI returned fewer than 3 domains
    if len(suggested_domains) < 3:
        clean_name = raw_query.split(".")[0].replace(" ", "")
        zones_to_use = target_tlds if target_tlds else ["com", "io", "co", "pro", "store"]
        for z in zones_to_use[:5]:
            suggested_domains.append(f"get{clean_name}.{z}")
            suggested_domains.append(f"{clean_name}app.{z}")
            suggested_domains.append(f"{clean_name}.{z}")

    unique_candidates = list(dict.fromkeys(suggested_domains))[:12]

    # Check live availability via Dynadot bulk search
    try:
        raw = await dynadot.bulk_search(unique_candidates)
    except Exception as exc:
        logger.warning("bulk_search for AI suggestions failed", extra={"error": str(exc)})
        raw = {"domain_result_list": []}

    items = _iter_search_items(raw, unique_candidates)
    results = [await _to_result(item, t, pricing_svc) for item in items]

    # Return only available domains with valid price
    available_only = [r for r in results if r.available and r.price_rub is not None][:6]

    return AiSuggestResponse(
        query=raw_query,
        keywords=keywords,
        candidates=unique_candidates,
        suggestions=available_only,
        total_checked=len(unique_candidates),
    )


@router.get("/categories", response_model=CategoriesResponse)
async def list_domain_categories(session: DbSession) -> CategoriesResponse:
    """Retrieve active domain categories for the search interface."""
    items = await get_all_categories_config(session)
    active = [c for c in items if c.get("is_enabled", True)]
    active.sort(key=lambda x: x.get("sort_order", 0))
    return CategoriesResponse(categories=[CategoryItem(**c) for c in active])


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
        logger.warning(
            "could not fetch live dynadot full catalog", extra={"error": str(exc)}
        )

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


@router.get("/{domain_id}/details", response_model=DomainDetailsResponse)
async def get_domain_details(
    domain_id: str,
    session: DbSession,
    t: Translate,
) -> DomainDetailsResponse:
    """Full domain management details including DNS records, privacy, lock, and renew price."""
    domain = await session.scalar(select(Domain).where(Domain.id == domain_id))
    if domain is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )

    pricing_svc = PricingService(session, settings)
    quote = await pricing_svc.quote(tld=domain.tld, cost_usd=10.0, years=1)

    records: list[DnsRecordItem] = []
    if domain.dns_records:
        try:
            raw_list = json.loads(domain.dns_records)
            if isinstance(raw_list, list):
                records = [DnsRecordItem(**r) for r in raw_list]
        except Exception:
            records = []
    else:
        # Default starter records if none configured yet
        records = [
            DnsRecordItem(type="A", host="@", value="76.76.21.21", ttl=300),
            DnsRecordItem(type="CNAME", host="www", value=domain.name, ttl=300),
        ]

    auth_code = f"EPP-{domain.name.replace('.', '-')}-{domain.id[:6].upper()}"

    return DomainDetailsResponse(
        id=domain.id,
        name=domain.name,
        tld=domain.tld,
        status=domain.status,
        registered_at=domain.registered_at,
        expires_at=domain.expires_at,
        auto_renew=domain.auto_renew,
        is_locked=domain.is_locked,
        privacy_enabled=domain.privacy_enabled,
        nameservers=domain.nameservers,
        dns_records=records,
        renew_price_rub=quote.price_rub,
        auth_code=auth_code,
    )


@router.put("/{domain_id}/dns", response_model=DomainDetailsResponse)
async def update_dns(
    domain_id: str,
    payload: DnsRecordsPayload,
    session: DbSession,
    dynadot: Dynadot,
    t: Translate,
) -> DomainDetailsResponse:
    """Save and sync custom DNS records."""
    domain = await session.scalar(select(Domain).where(Domain.id == domain_id))
    if domain is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )

    service = RegistrationService(session, dynadot, settings)
    records_dict = [r.model_dump() for r in payload.records]
    domain = await service.update_dns_records(domain, records_dict)
    return await get_domain_details(domain.id, session, t)


@router.post("/{domain_id}/quick-preset", response_model=DomainDetailsResponse)
async def apply_quick_preset(
    domain_id: str,
    payload: DomainQuickPresetPayload,
    session: DbSession,
    dynadot: Dynadot,
    t: Translate,
) -> DomainDetailsResponse:
    """Apply a 1-click preset configuration (VPS, Telegram redirect, Yandex 360, Cloudflare)."""
    domain = await session.scalar(select(Domain).where(Domain.id == domain_id))
    if domain is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )

    service = RegistrationService(session, dynadot, settings)
    p_type = payload.preset_type.lower()

    if p_type == "vps":
        ip = (payload.target or "45.192.12.105").strip()
        records = [
            {"type": "A", "host": "@", "value": ip, "ttl": 300},
            {"type": "A", "host": "www", "value": ip, "ttl": 300},
        ]
        domain = await service.update_dns_records(domain, records)
    elif p_type == "telegram":
        raw_target = (payload.target or "").strip()
        if not raw_target:
            raw_target = "https://t.me/"
        elif not raw_target.startswith(("http://", "https://")):
            raw_target = f"https://{raw_target}"

        # 1. Update or create DomainRedirect
        existing_redir = await session.scalar(
            select(DomainRedirect).where(DomainRedirect.domain_id == domain.id)
        )
        if existing_redir:
            existing_redir.target_url = raw_target
            existing_redir.is_active = True
        else:
            session.add(
                DomainRedirect(
                    domain_id=domain.id,
                    domain_name=domain.name,
                    target_url=raw_target,
                    http_status=302,
                    is_active=True,
                )
            )
        await session.commit()

        # Point DNS records
        records = [
            {"type": "CNAME", "host": "@", "value": "t.me", "ttl": 300},
            {"type": "TXT", "host": "_telegram", "value": raw_target, "ttl": 300},
        ]
        domain = await service.update_dns_records(domain, records)
    elif p_type == "yandex_mail":
        records = [
            {
                "type": "MX",
                "host": "@",
                "value": "mx.yandex.net.",
                "ttl": 300,
                "priority": 10,
            },
            {
                "type": "TXT",
                "host": "@",
                "value": "v=spf1 redirect=_spf.yandex.net",
                "ttl": 300,
            },
            {
                "type": "CNAME",
                "host": "mail",
                "value": "domain.mail.yandex.net.",
                "ttl": 300,
            },
        ]
        domain = await service.update_dns_records(domain, records)
    elif p_type == "improvmx":
        records = [
            {
                "type": "MX",
                "host": "@",
                "value": "mx1.improvmx.com.",
                "ttl": 300,
                "priority": 10,
            },
            {
                "type": "MX",
                "host": "@",
                "value": "mx2.improvmx.com.",
                "ttl": 300,
                "priority": 20,
            },
            {
                "type": "TXT",
                "host": "@",
                "value": "v=spf1 include:spf.improvmx.com ~all",
                "ttl": 300,
            },
        ]
        domain = await service.update_dns_records(domain, records)
    elif p_type == "cloudflare_mail":
        records = [
            {
                "type": "MX",
                "host": "@",
                "value": "route1.mx.cloudflare.net.",
                "ttl": 300,
                "priority": 10,
            },
            {
                "type": "MX",
                "host": "@",
                "value": "route2.mx.cloudflare.net.",
                "ttl": 300,
                "priority": 20,
            },
            {
                "type": "MX",
                "host": "@",
                "value": "route3.mx.cloudflare.net.",
                "ttl": 300,
                "priority": 30,
            },
            {
                "type": "TXT",
                "host": "@",
                "value": "v=spf1 include:_spf.mx.cloudflare.net ~all",
                "ttl": 300,
            },
        ]
        domain = await service.update_dns_records(domain, records)
    elif p_type == "cloudflare":
        cf_ns = ["ada.ns.cloudflare.com", "walt.ns.cloudflare.com"]
        domain = await service.update_nameservers(domain, cf_ns)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported preset"
        )

    return await get_domain_details(domain.id, session, t)


@router.put("/{domain_id}/privacy", response_model=DomainDetailsResponse)
async def toggle_privacy(
    domain_id: str,
    payload: DomainPrivacyPayload,
    session: DbSession,
    dynadot: Dynadot,
    t: Translate,
) -> DomainDetailsResponse:
    domain = await session.scalar(select(Domain).where(Domain.id == domain_id))
    if domain is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )
    service = RegistrationService(session, dynadot, settings)
    await service.update_privacy(domain, payload.enabled)
    return await get_domain_details(domain.id, session, t)


@router.put("/{domain_id}/lock", response_model=DomainDetailsResponse)
async def toggle_lock(
    domain_id: str,
    payload: DomainLockPayload,
    session: DbSession,
    dynadot: Dynadot,
    t: Translate,
) -> DomainDetailsResponse:
    domain = await session.scalar(select(Domain).where(Domain.id == domain_id))
    if domain is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )
    service = RegistrationService(session, dynadot, settings)
    await service.update_lock(domain, payload.locked)
    return await get_domain_details(domain.id, session, t)


@router.post("/{domain_id}/renew", response_model=DomainRenewResponse)
async def renew_domain_order(
    domain_id: str,
    payload: DomainRenewPayload,
    telegram_id: int,
    session: DbSession,
    dynadot: Dynadot,
    t: Translate,
) -> DomainRenewResponse:
    """Create a renewal order with price frozen and generate SBP checkout."""
    domain = await session.scalar(select(Domain).where(Domain.id == domain_id))
    if domain is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )

    pricing_svc = PricingService(session, settings)
    quote = await pricing_svc.quote(tld=domain.tld, cost_usd=10.0, years=payload.years)

    order = Order(
        user_id=user.id,
        domain_name=domain.name,
        tld=domain.tld,
        years=payload.years,
        status=OrderStatus.AWAITING_PAYMENT,
        cost_usd=quote.cost_usd,
        price_rub=quote.price_rub,
        markup_percent=quote.markup_percent,
        fx_rate=quote.fx_rate,
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)

    # Platega payment link placeholder
    payment_url = f"https://app.platega.io/pay/{order.id}"

    return DomainRenewResponse(
        order_id=order.id,
        amount_rub=order.price_rub,
        payment_url=payment_url,
    )


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

    # Extract wholesale registration and renewal price in USD
    cost_usd: float | None = None
    renewal_cost_usd: float | None = None
    price_list = item.get("price_list")
    if isinstance(price_list, list) and len(price_list) > 0:
        p1 = next(
            (p for p in price_list if "1 year" in str(p.get("unit", "")).lower()),
            price_list[0],
        )
        reg_p = p1.get("registration_price") or p1.get("price")
        renew_p = p1.get("renewal_price")
        if reg_p is not None:
            try:
                cost_usd = float(reg_p)
            except (ValueError, TypeError):
                cost_usd = None
        if renew_p is not None:
            try:
                renewal_cost_usd = float(renew_p)
            except (ValueError, TypeError):
                renewal_cost_usd = None

    if cost_usd is None:
        raw_p = item.get("price") or item.get("register_price")
        try:
            cost_usd = float(raw_p) if raw_p is not None else None
        except (ValueError, TypeError):
            cost_usd = None

    # Calculate resale price in RUB through PricingService
    quote = await pricing_svc.quote(tld=tld, cost_usd=cost_usd or 0.0, years=1)
    renew_quote = await pricing_svc.quote_renewal(
        tld=tld, cost_usd=renewal_cost_usd, years=1
    )

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

    renewal_price_rub = renew_quote.price_rub if renew_quote else None
    renewal_price_usd = renew_quote.cost_usd if renew_quote else None

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
        renewal_price_rub=renewal_price_rub,
        renewal_price_usd=renewal_price_usd,
        message=msg,
    )


# ---------------------------------------------------------------- redirects


@router.get("/{domain_id}/redirect", response_model=DomainRedirectResponse)
async def get_domain_redirect(
    domain_id: str,
    session: DbSession,
    t: Translate,
) -> DomainRedirectResponse:
    """Get active redirect configuration for domain."""
    redir = await session.scalar(
        select(DomainRedirect).where(DomainRedirect.domain_id == domain_id)
    )
    if redir is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No redirect configured"
        )
    return DomainRedirectResponse(
        domain_id=redir.domain_id,
        domain_name=redir.domain_name,
        target_url=redir.target_url,
        http_status=redir.http_status,
        is_active=redir.is_active,
    )


@router.put("/{domain_id}/redirect", response_model=DomainRedirectResponse)
async def set_domain_redirect(
    domain_id: str,
    payload: DomainRedirectUpdate,
    session: DbSession,
    dynadot: Dynadot,
    t: Translate,
) -> DomainRedirectResponse:
    """Configure or toggle HTTP 301/302 URL/Telegram redirection for domain."""
    domain = await session.scalar(select(Domain).where(Domain.id == domain_id))
    if domain is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )

    target = payload.target_url.strip()
    if not target.startswith(("http://", "https://")):
        target = f"https://{target}"

    redir = await session.scalar(
        select(DomainRedirect).where(DomainRedirect.domain_id == domain_id)
    )
    if redir:
        redir.target_url = target
        redir.http_status = payload.http_status
        redir.is_active = payload.is_active
    else:
        redir = DomainRedirect(
            domain_id=domain.id,
            domain_name=domain.name,
            target_url=target,
            http_status=payload.http_status,
            is_active=payload.is_active,
        )
        session.add(redir)
    await session.commit()
    await session.refresh(redir)

    if redir.is_active:
        service = RegistrationService(session, dynadot, settings)
        records = [
            {"type": "A", "host": "@", "value": "45.192.12.105", "ttl": 300},
            {"type": "A", "host": "www", "value": "45.192.12.105", "ttl": 300},
            {"type": "TXT", "host": "_telegram", "value": target, "ttl": 300},
        ]
        try:
            await service.update_dns_records(domain, records)
        except Exception as exc:
            logger.warning(
                "failed to set redirect dns records", extra={"error": str(exc)}
            )

    return DomainRedirectResponse(
        domain_id=redir.domain_id,
        domain_name=redir.domain_name,
        target_url=redir.target_url,
        http_status=redir.http_status,
        is_active=redir.is_active,
    )


@router.delete("/{domain_id}/redirect")
async def delete_domain_redirect(
    domain_id: str,
    session: DbSession,
) -> dict[str, bool]:
    """Delete redirection for domain."""
    redir = await session.scalar(
        select(DomainRedirect).where(DomainRedirect.domain_id == domain_id)
    )
    if redir:
        await session.delete(redir)
        await session.commit()
    return {"ok": True}


# ---------------------------------------------------------------- watchlist


@router.post("/watchlist", response_model=DomainWatchlistResponse)
async def add_to_watchlist(
    payload: DomainWatchlistRequest,
    session: DbSession,
) -> DomainWatchlistResponse:
    """Add a taken domain to user watchlist to notify when it becomes available."""
    clean_domain = payload.domain.strip().lower()
    item = await session.scalar(
        select(DomainWatchlist).where(
            DomainWatchlist.telegram_id == payload.telegram_id,
            DomainWatchlist.domain == clean_domain,
        )
    )
    if item:
        item.is_active = True
    else:
        item = DomainWatchlist(
            telegram_id=payload.telegram_id,
            domain=clean_domain,
            is_active=True,
        )
        session.add(item)
    await session.commit()
    await session.refresh(item)
    return DomainWatchlistResponse(
        id=item.id,
        domain=item.domain,
        telegram_id=item.telegram_id,
        is_active=item.is_active,
        created_at=item.created_at,
    )


@router.get("/watchlist", response_model=list[DomainWatchlistResponse])
async def list_watchlist(
    telegram_id: int,
    session: DbSession,
) -> list[DomainWatchlistResponse]:
    """List domains monitored by user."""
    items = list(
        await session.scalars(
            select(DomainWatchlist)
            .where(
                DomainWatchlist.telegram_id == telegram_id,
                DomainWatchlist.is_active.is_(True),
            )
            .order_by(DomainWatchlist.created_at.desc())
        )
    )
    return [
        DomainWatchlistResponse(
            id=i.id,
            domain=i.domain,
            telegram_id=i.telegram_id,
            is_active=i.is_active,
            created_at=i.created_at,
        )
        for i in items
    ]


@router.delete("/watchlist/{domain}")
async def remove_from_watchlist(
    domain: str,
    telegram_id: int,
    session: DbSession,
) -> dict[str, bool]:
    """Remove a domain from user watchlist."""
    clean_domain = domain.strip().lower()
    item = await session.scalar(
        select(DomainWatchlist).where(
            DomainWatchlist.telegram_id == telegram_id,
            DomainWatchlist.domain == clean_domain,
        )
    )
    if item:
        item.is_active = False
        await session.commit()
    return {"ok": True}
