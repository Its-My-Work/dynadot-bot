"""In-memory VPS provider used for local runs, tests and unconfigured deploys.

The order flow must work end to end without any upstream credentials —
otherwise nobody can develop the checkout, and a production misconfiguration
would turn into a 500 instead of a degraded-but-working catalogue. This
provider fakes the one behaviour that matters for the UI: a server starts out
without an IP and only becomes ACTIVE after the first status poll, exactly like
a real VM finishing cloud-init.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.integrations.vps.base import (
    BaseVpsProvider,
    VpsApiError,
    VpsLocation,
    VpsPreset,
    VpsServer,
)
from app.integrations.vps.catalog import DEFAULT_LOCATIONS, DEFAULT_PRESETS, find_preset
from app.models.enums import ServerStatus, VpsProvider


class MockVpsProvider(BaseVpsProvider):
    """Deterministic, side-effect-free provider."""

    provider = VpsProvider.MOCK

    def __init__(self, *, auto_activate: bool = True) -> None:
        #: When False, servers stay INSTALLING forever — handy for testing the
        #: "still provisioning" branch of the UI.
        self._auto_activate = auto_activate
        self._servers: dict[str, VpsServer] = {}
        self._cloud_init: dict[str, str] = {}

    async def list_locations(self) -> list[VpsLocation]:
        return list(DEFAULT_LOCATIONS)

    async def list_presets(self) -> list[VpsPreset]:
        return list(DEFAULT_PRESETS)

    async def create_server(
        self,
        location: str,
        preset: str,
        cloud_init: str,
        name: str,
    ) -> VpsServer:
        plan = find_preset(preset)
        if plan is None:
            raise VpsApiError(f"unknown preset {preset!r}")

        external_id = f"mock-{uuid.uuid4().hex[:12]}"
        server = VpsServer(
            external_id=external_id,
            name=name,
            status=ServerStatus.INSTALLING,
            ip_address=None,
            location_code=location,
            location_name=location.upper(),
            preset_name=plan.name,
            cpu=plan.cpu,
            ram_mb=plan.ram_mb,
            disk_gb=plan.disk_gb,
            raw={"mock": True, "cloud_init_bytes": len(cloud_init)},
        )
        self._servers[external_id] = server
        self._cloud_init[external_id] = cloud_init
        return server

    async def get_server(self, external_id: str) -> VpsServer:
        server = self._servers.get(external_id)
        if server is None:
            raise VpsApiError(f"server {external_id!r} not found", status_code=404)

        if self._auto_activate and server.status is ServerStatus.INSTALLING:
            # A deterministic fake IP derived from the id keeps test assertions
            # stable while still being unique per server.
            octet = int(external_id[-2:], 16) % 254 + 1
            server.ip_address = f"203.0.113.{octet}"
            server.status = ServerStatus.ACTIVE
        return server

    async def delete_server(self, external_id: str) -> bool:
        server = self._servers.pop(external_id, None)
        self._cloud_init.pop(external_id, None)
        if server is None:
            return False
        server.status = ServerStatus.DELETED
        return True

    # ------------------------------------------------------------ test helpers

    def cloud_init_for(self, external_id: str) -> str | None:
        """Return the user-data the caller passed — asserted on in tests."""
        return self._cloud_init.get(external_id)

    def snapshot(self) -> dict[str, Any]:
        return {sid: server.status.value for sid, server in self._servers.items()}


__all__ = ["MockVpsProvider"]
