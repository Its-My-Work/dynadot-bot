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

# Default zones shown in the TMA grid until admin pricing overrides them.
DEFAULT_TLDS = ["com", "net", "org", "io", "dev", "ru"]


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
    """Check availability of a name across the selected zones.

    Uses ``bulk_search`` so one queued request covers every zone, rather than
    N serialised single searches.
    """
    name = payload.query
    if "." in name:
        base, _, tld = name.rpartition(".")
        if not base:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=t("search.invalid_name"),
            )
        candidates = [f"{base}.{tld}"]
    else:
        tlds = payload.tlds or DEFAULT_TLDS
        candidates = [f"{name}.{tld.lstrip('.')}" for tld in tlds]

    try:
        raw = await dynadot.bulk_search(candidates)
    except DynadotError as exc:
        logger.warning("domain search failed", extra={"error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=t("error.registrar_unavailable"),
        ) from exc

    results = [_to_result(item, t) for item in _iter_search_items(raw, candidates)]
    return DomainSearchResponse(query=name, results=results)


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


def _to_result(item: dict, t) -> DomainSearchResult:
    domain = str(item.get("domain_name") or item.get("domain") or "")
    available = item.get("available")
    if isinstance(available, str):
        available = available.lower() in ("yes", "true", "1")

    price_usd = item.get("price") or item.get("register_price")
    try:
        price_usd = float(price_usd) if price_usd is not None else None
    except (TypeError, ValueError):
        price_usd = None

    return DomainSearchResult(
        domain=domain,
        tld=domain.rpartition(".")[2],
        available=bool(available),
        is_premium=bool(item.get("is_premium") or item.get("premium")),
        price_usd=price_usd,
        price_rub=_apply_markup(price_usd) if price_usd else None,
        message=t("search.available") if available else t("search.taken"),
    )
