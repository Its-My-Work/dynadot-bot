"""Domain search endpoints.

Scaffold: wiring, validation and i18n are real; pricing and persistence land
with the pricing service.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession, Dynadot, Translate
from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.dynadot.exceptions import DynadotError
from app.schemas import DomainSearchRequest, DomainSearchResponse, DomainSearchResult

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

    results = [
        _to_result(item, t)
        for item in _iter_search_items(raw, candidates)
    ]
    return DomainSearchResponse(query=name, results=results)


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
