"""VPN server catalogue, ordering and management endpoints.

The catalogue endpoints are intentionally tolerant: if the upstream provider is
unreachable they fall back to the static retail catalogue, because a customer
browsing plans should never see an error page for something we already know the
answer to.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import DbSession, Payments, Translate, Vps
from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.payments.base import PaymentIntent
from app.integrations.payments.exceptions import PaymentError
from app.integrations.vps.base import VpsProviderError
from app.integrations.vps.catalog import DEFAULT_LOCATIONS, DEFAULT_PRESETS
from app.models.enums import Currency, ServerStatus
from app.models.user import User
from app.models.vps import ServerInstance
from app.schemas import (
    ServerOrderCreate,
    ServerOrderResponse,
    ServerRead,
    VpsLocationRead,
    VpsPresetRead,
)
from app.services.servers import ServerService

logger = get_logger(__name__)
router = APIRouter(prefix="/servers", tags=["servers"])


async def _require_user(session, telegram_id: int, t) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )
    if user.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=t("error.forbidden")
        )
    return user


# ------------------------------------------------------------------- catalogue


@router.get("/locations", response_model=list[VpsLocationRead])
async def list_locations(provider: Vps) -> list[VpsLocationRead]:
    """Available datacentres, with flags for the TMA picker."""
    try:
        locations = await provider.list_locations()
    except VpsProviderError as exc:
        logger.warning("location lookup failed", extra={"error": str(exc)})
        locations = list(DEFAULT_LOCATIONS)

    return [
        VpsLocationRead(
            code=loc.code,
            name=loc.name,
            country=loc.country,
            flag=loc.flag,
            label=loc.label,
            available=loc.available,
        )
        for loc in locations
    ]


@router.get("/presets", response_model=list[VpsPresetRead])
async def list_presets(provider: Vps) -> list[VpsPresetRead]:
    """Sellable plans: Start 790 ₽, Standard 1290 ₽, Pro 2490 ₽."""
    try:
        presets = await provider.list_presets()
    except VpsProviderError as exc:
        logger.warning("preset lookup failed", extra={"error": str(exc)})
        presets = list(DEFAULT_PRESETS)

    return [
        VpsPresetRead(
            code=p.code,
            name=p.name,
            cpu=p.cpu,
            ram_mb=p.ram_mb,
            disk_gb=p.disk_gb,
            price_rub=p.price_rub,
            bandwidth_tb=p.bandwidth_tb,
            description=p.description,
        )
        for p in presets
    ]


# ---------------------------------------------------------------------- orders


@router.post(
    "/orders",
    response_model=ServerOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_server_order(
    payload: ServerOrderCreate,
    telegram_id: int,
    session: DbSession,
    provider: Vps,
    gateway: Payments,
    t: Translate,
) -> ServerOrderResponse:
    """Create a server order and start its Platega checkout.

    The row is persisted before the gateway is called so a payment-link failure
    leaves an auditable PENDING order rather than nothing. When no gateway is
    configured (local/dev) the server is provisioned straight away, otherwise
    provisioning waits for the payment webhook.
    """
    user = await _require_user(session, telegram_id, t)
    service = ServerService(session, provider, settings)

    try:
        server = await service.create_order(
            user_id=user.id,
            location=payload.location,
            preset=payload.preset,
            name=payload.name,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    amount = float(server.price_rub or 0)
    payment_url: str | None = None
    payment_id: str | None = None

    try:
        session_data = await gateway.create_payment(
            PaymentIntent(
                order_id=server.id,
                amount=amount,
                currency=Currency.RUB,
                description=f"VPN {server.preset_name} · {server.location_name}",
                user_id=str(user.telegram_id),
                user_name=f"@{user.username}" if user.username else None,
                method=settings.platega_payment_method,
            )
        )
        payment_url = session_data.payment_url
        payment_id = session_data.external_id
    except PaymentError as exc:
        # No gateway (or a gateway outage) must not block development or a
        # manually-settled order: provision now and let the admin reconcile.
        logger.warning(
            "server checkout unavailable; provisioning directly",
            extra={"server_id": server.id, "error": str(exc)},
        )
        try:
            await service.provision(server)
        except VpsProviderError:
            # Status is already FAILED on the row; the response still carries
            # the order so the customer/admin can see what was attempted.
            logger.error("direct provisioning failed", extra={"server_id": server.id})

    await session.commit()
    await session.refresh(server)

    return ServerOrderResponse(
        server=ServerRead.model_validate(server),
        payment_url=payment_url,
        payment_id=payment_id,
        amount=amount,
    )


# ------------------------------------------------------------------ management


@router.get("/my", response_model=list[ServerRead])
async def list_my_servers(
    telegram_id: int,
    session: DbSession,
    provider: Vps,
    t: Translate,
    include_deleted: bool = Query(default=False),
) -> list[ServerInstance]:
    """List the customer's servers, refreshing anything still provisioning."""
    user = await _require_user(session, telegram_id, t)
    service = ServerService(session, provider, settings)
    servers = await service.list_for_user(user.id, include_deleted=include_deleted)

    # Only poll servers that can still change: an IP arriving late is the whole
    # reason this endpoint syncs at all.
    for server in servers:
        if server.status in {ServerStatus.PENDING, ServerStatus.INSTALLING}:
            await service.sync(server)
    await session.commit()
    return servers


@router.get("/{server_id}", response_model=ServerRead)
async def get_server(
    server_id: str,
    telegram_id: int,
    session: DbSession,
    provider: Vps,
    t: Translate,
) -> ServerInstance:
    """Fetch one server, syncing its live state first."""
    user = await _require_user(session, telegram_id, t)
    server = await session.scalar(
        select(ServerInstance).where(
            ServerInstance.id == server_id,
            ServerInstance.user_id == user.id,
        )
    )
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )

    service = ServerService(session, provider, settings)
    if server.status in {ServerStatus.PENDING, ServerStatus.INSTALLING}:
        await service.sync(server)
        await session.commit()
    return server


@router.post("/{server_id}/delete", response_model=ServerRead)
async def delete_server(
    server_id: str,
    telegram_id: int,
    session: DbSession,
    provider: Vps,
    t: Translate,
) -> ServerInstance:
    """Destroy a server at the provider and close out the row."""
    user = await _require_user(session, telegram_id, t)
    server = await session.scalar(
        select(ServerInstance).where(
            ServerInstance.id == server_id,
            ServerInstance.user_id == user.id,
        )
    )
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=t("error.not_found")
        )

    service = ServerService(session, provider, settings)
    try:
        await service.delete(server)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    await session.commit()
    return server
