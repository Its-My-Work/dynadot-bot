"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.i18n import Translator, normalize_language, parse_accept_language
from app.integrations.dynadot import DynadotClient
from app.integrations.payments import PaymentGateway

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_language(
    accept_language: Annotated[str | None, Header(alias="Accept-Language")] = None,
    x_language: Annotated[str | None, Header(alias="X-Language")] = None,
) -> str:
    """Resolve the request language.

    An explicit ``X-Language`` header (set by the TMA from the saved profile
    preference) wins over the browser's ``Accept-Language``.
    """
    if x_language:
        return normalize_language(x_language)
    return parse_accept_language(accept_language)


Language = Annotated[str, Depends(get_language)]


async def get_translator_dep(language: Language) -> Translator:
    return Translator(language)


Translate = Annotated[Translator, Depends(get_translator_dep)]


async def get_dynadot_client(request: Request) -> DynadotClient:
    """Return the shared client created during application startup."""
    client = getattr(request.app.state, "dynadot_client", None)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dynadot client is not initialised",
        )
    return client


Dynadot = Annotated[DynadotClient, Depends(get_dynadot_client)]


async def get_payment_gateway(request: Request) -> PaymentGateway:
    """Return the shared payment gateway created during application startup."""
    gateway = getattr(request.app.state, "payment_gateway", None)
    if gateway is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Payment gateway is not initialised",
        )
    return gateway


Payments = Annotated[PaymentGateway, Depends(get_payment_gateway)]
