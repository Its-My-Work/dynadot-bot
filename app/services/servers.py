"""Server ordering and provisioning.

The flow is deliberately split in two: ``create_order`` persists a PENDING row
with its Reality keys and price frozen, and ``provision`` is what actually
talks to the upstream cloud after the payment webhook confirms the money. That
separation is what makes a failed provider call recoverable — the paid row
already exists and can be retried or refunded, instead of the charge going
through with nothing to show for it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.integrations.vps.base import (
    BaseVpsProvider,
    VpsPreset,
    VpsProviderError,
)
from app.integrations.vps.catalog import find_location, find_preset
from app.integrations.vps.xray import (
    DEFAULT_SNI,
    build_cloud_init,
    build_vless_uri,
    generate_credentials,
)
from app.models.enums import ServerStatus
from app.models.vps import ServerInstance

logger = get_logger(__name__)

#: Statuses from which a customer-triggered delete is allowed.
DELETABLE = {
    ServerStatus.PENDING,
    ServerStatus.INSTALLING,
    ServerStatus.ACTIVE,
    ServerStatus.SUSPENDED,
    ServerStatus.FAILED,
}


class ServerService:
    """Business logic for renting and tearing down VPN servers."""

    def __init__(
        self,
        session: AsyncSession,
        provider: BaseVpsProvider,
        settings,
    ) -> None:
        self._session = session
        self._provider = provider
        self._settings = settings

    # ------------------------------------------------------------------ order

    async def create_order(
        self,
        *,
        user_id: str,
        location: str,
        preset: str,
        name: str | None = None,
    ) -> ServerInstance:
        """Persist a PENDING server with its keys and frozen price."""
        plan = find_preset(preset)
        if plan is None:
            raise ValueError(f"unknown preset {preset!r}")
        place = find_location(location)
        if place is None:
            raise ValueError(f"unknown location {location!r}")

        sni = self._settings.vps_reality_sni or DEFAULT_SNI
        creds = generate_credentials(sni)

        server = ServerInstance(
            user_id=user_id,
            provider=self._provider.provider,
            name=name or f"vpn-{place.code}-{plan.code}",
            location_code=place.code,
            location_name=place.name,
            status=ServerStatus.PENDING,
            preset_name=plan.name,
            cpu=plan.cpu,
            ram_mb=plan.ram_mb,
            disk_gb=plan.disk_gb,
            price_rub=Decimal(str(plan.price_rub)),
            vless_uuid=creds.uuid,
            vless_public_key=creds.public_key,
            vless_short_id=creds.short_id,
            vless_sni=creds.sni,
        )
        # The private key is never persisted: it only has to reach the VM once,
        # inside cloud-init. Keeping it out of the database means a dump of this
        # table cannot be used to impersonate anyone's server.
        self._session.add(server)
        await self._session.flush()
        logger.info(
            "server order created",
            extra={"server_id": server.id, "preset": plan.code},
        )
        return server

    # ------------------------------------------------------------- provisionin

    async def provision(
        self, server: ServerInstance, *, private_key: str | None = None
    ) -> ServerInstance:
        """Create the VM upstream and store what came back.

        ``private_key`` is normally regenerated here because the order step did
        not keep it; passing one in is how a rebuild reuses existing client
        credentials.
        """
        plan = find_preset(_preset_code(server)) or _plan_from_row(server)

        if private_key is None:
            # A fresh server key with the *same* client uuid/short id keeps the
            # customer's saved link working only if the public half matches, so
            # re-key both halves together.
            creds = generate_credentials(server.vless_sni or DEFAULT_SNI)
            private_key = creds.private_key
            server.vless_public_key = creds.public_key
            server.vless_short_id = creds.short_id

        cloud_init = build_cloud_init(
            client_uuid=server.vless_uuid or "",
            private_key=private_key,
            short_id=server.vless_short_id or "",
            sni=server.vless_sni or DEFAULT_SNI,
            hostname=server.name,
        )

        try:
            created = await self._provider.create_server(
                location=server.location_code or "nl",
                preset=plan.code,
                cloud_init=cloud_init,
                name=server.name,
            )
        except VpsProviderError as exc:
            server.status = ServerStatus.FAILED
            await self._session.flush()
            logger.error(
                "server provisioning failed",
                extra={"server_id": server.id, "error": str(exc)},
            )
            raise

        server.external_id = created.external_id
        server.status = created.status
        server.ip_address = created.ip_address
        if created.location_name:
            server.location_name = created.location_name
        server.expires_at = datetime.now(UTC) + timedelta(
            days=self._settings.vps_term_days
        )
        self._refresh_link(server)
        await self._session.flush()
        return server

    async def sync(self, server: ServerInstance) -> ServerInstance:
        """Poll the provider and update IP/status.

        Called when the customer opens their server list: a VM that was still
        installing at order time usually has its address by then, and without
        the IP the VLESS link cannot be built at all.
        """
        if not server.external_id:
            return server
        try:
            remote = await self._provider.get_server(server.external_id)
        except VpsProviderError as exc:
            logger.warning(
                "server sync failed",
                extra={"server_id": server.id, "error": str(exc)},
            )
            return server

        server.status = remote.status
        if remote.ip_address:
            server.ip_address = remote.ip_address
        self._refresh_link(server)
        await self._session.flush()
        return server

    async def delete(self, server: ServerInstance) -> ServerInstance:
        """Destroy the VM upstream and mark the row DELETED."""
        if server.status is ServerStatus.DELETED:
            return server
        if server.status not in DELETABLE:
            raise ValueError(f"server {server.id} cannot be deleted")

        if server.external_id:
            try:
                await self._provider.delete_server(server.external_id)
            except VpsProviderError as exc:
                # A provider error must not strand the customer with a server
                # they asked to remove; the row is closed out and the orphan is
                # reconciled by the admin panel.
                logger.error(
                    "server delete failed upstream",
                    extra={"server_id": server.id, "error": str(exc)},
                )

        server.status = ServerStatus.DELETED
        await self._session.flush()
        return server

    # ---------------------------------------------------------------- queries

    async def list_for_user(
        self, user_id: str, *, include_deleted: bool = False
    ) -> list[ServerInstance]:
        stmt = select(ServerInstance).where(ServerInstance.user_id == user_id)
        if not include_deleted:
            stmt = stmt.where(ServerInstance.status != ServerStatus.DELETED)
        stmt = stmt.order_by(ServerInstance.created_at.desc())
        return list((await self._session.scalars(stmt)).all())

    # ---------------------------------------------------------------- helpers

    def _refresh_link(self, server: ServerInstance) -> None:
        """Rebuild ``vless_link`` once every ingredient is known."""
        if not (server.ip_address and server.vless_uuid and server.vless_public_key):
            return
        server.vless_link = build_vless_uri(
            client_uuid=server.vless_uuid,
            ip_address=server.ip_address,
            public_key=server.vless_public_key,
            short_id=server.vless_short_id or "",
            sni=server.vless_sni or DEFAULT_SNI,
            name=server.name,
        )


def _preset_code(server: ServerInstance) -> str:
    return (server.preset_name or "start").strip().lower()


def _plan_from_row(server: ServerInstance) -> VpsPreset:
    """Fallback plan built from the stored hardware, for renamed presets."""
    return VpsPreset(
        code=_preset_code(server),
        name=server.preset_name or "Start",
        cpu=server.cpu or 1,
        ram_mb=server.ram_mb or 1024,
        disk_gb=server.disk_gb or 15,
        price_rub=float(server.price_rub or 0),
    )


__all__ = ["DELETABLE", "ServerService"]
