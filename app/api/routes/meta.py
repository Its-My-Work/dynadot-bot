"""Health, version and i18n metadata endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DbSession, Translate
from app.core.config import settings
from app.core.themes import themes_response
from app.i18n import available_languages
from app.schemas import HealthResponse, LanguagesResponse, ThemesResponse

router = APIRouter(tags=["meta"])

VERSION = "0.1.0"


@router.get("/health", response_model=HealthResponse)
async def health(session: DbSession) -> HealthResponse:
    """Liveness probe that also verifies database connectivity."""
    try:
        await session.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        database = "error"

    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        environment=settings.environment,
        version=VERSION,
        database=database,
        dynadot_configured=settings.dynadot_configured,
    )


@router.get("/languages", response_model=LanguagesResponse)
async def languages() -> LanguagesResponse:
    return LanguagesResponse(
        default=settings.default_language, supported=available_languages()
    )


@router.get("/themes", response_model=ThemesResponse)
async def themes() -> ThemesResponse:
    """The selectable design themes, for the TMA/admin theme picker."""
    return themes_response()


@router.get("/ping")
async def ping(t: Translate) -> dict[str, str]:
    """Smoke-test endpoint demonstrating request-scoped localisation."""
    return {"message": t("common.continue"), "language": t.language}
