"""Timeweb Cloud VPS adapter (https://api.timeweb.cloud/api/v1).

Timeweb is the primary upstream for RU-adjacent locations. Two of its quirks
drive the code below: server creation takes a *configurator* (cpu/ram/disk
chosen à la carte) rather than a fixed plan id, and ``cloud_init`` is accepted
directly on the create call, so no post-boot SSH step is needed.
"""

from __future__ import annotations

from typing import Any

from app.integrations.vps.base import VpsApiError, VpsLocation, VpsPreset, VpsServer
from app.integrations.vps.catalog import (
    DEFAULT_LOCATIONS,
    DEFAULT_PRESETS,
    find_location,
    find_preset,
)
from app.integrations.vps.http import HttpVpsProvider, as_int, first_ipv4
from app.models.enums import ServerStatus, VpsProvider

#: Timeweb's own words for server state, mapped onto ours.
STATUS_MAP = {
    "installing": ServerStatus.INSTALLING,
    "software_install": ServerStatus.INSTALLING,
    "configuring": ServerStatus.INSTALLING,
    "creating": ServerStatus.PENDING,
    "on": ServerStatus.ACTIVE,
    "started": ServerStatus.ACTIVE,
    "on_hold": ServerStatus.SUSPENDED,
    "blocked": ServerStatus.SUSPENDED,
    "off": ServerStatus.SUSPENDED,
    "stopped": ServerStatus.SUSPENDED,
    "no_paid": ServerStatus.SUSPENDED,
    "deleted": ServerStatus.DELETED,
    "removing": ServerStatus.DELETED,
    "failed": ServerStatus.FAILED,
    "error": ServerStatus.FAILED,
}

#: Location code -> Timeweb availability-zone id.
ZONE_MAP = {
    "nl": "ams-1",
    "pl": "waw-1",
    "kz": "ala-1",
    "tr": "ist-1",
    "fi": "hel-1",
}

#: Ubuntu 22.04 is the default image: it is the oldest release the official
#: Xray installer still ships a unit file for, and it boots fastest.
DEFAULT_OS_NAME = "ubuntu"
DEFAULT_OS_VERSION = "22.04"


def map_status(raw: Any) -> ServerStatus:
    if not raw:
        return ServerStatus.PENDING
    return STATUS_MAP.get(str(raw).strip().lower(), ServerStatus.PENDING)


class TimewebVpsProvider(HttpVpsProvider):
    """Timeweb Cloud implementation."""

    provider = VpsProvider.TIMEWEB
    base_url = "https://api.timeweb.cloud/api/v1"

    async def list_locations(self) -> list[VpsLocation]:
        """Return our catalogue, flagged with what Timeweb actually has.

        We never surface raw upstream zones: the customer picks from our fixed
        five, and anything Timeweb is out of capacity for is marked
        unavailable rather than hidden, so the UI can explain itself.
        """
        try:
            payload = await self._request("GET", "/presets/servers")
        except VpsApiError:
            return list(DEFAULT_LOCATIONS)

        presets = _as_list(payload, "server_presets", "presets")
        live_zones = {
            str(item.get("location") or "").lower()
            for item in presets
            if isinstance(item, dict)
        }
        return [
            VpsLocation(
                code=loc.code,
                name=loc.name,
                country=loc.country,
                flag=loc.flag,
                available=not live_zones or ZONE_MAP.get(loc.code, "") in live_zones,
            )
            for loc in DEFAULT_LOCATIONS
        ]

    async def list_presets(self) -> list[VpsPreset]:
        """Our retail tiers — Timeweb is billed per configurator, not per plan."""
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

        zone = ZONE_MAP.get(location.lower())
        if zone is None:
            raise VpsApiError(f"unsupported location {location!r}")

        os_id = await self._resolve_os_id()
        body: dict[str, Any] = {
            "name": name,
            "comment": "VPN (Xray Reality)",
            "availability_zone": zone,
            "os_id": os_id,
            "software_id": None,
            "bandwidth": 200,
            "is_ddos_guard": False,
            "is_local_network": False,
            "cloud_init": cloud_init,
            "configuration": {
                "configurator_id": await self._resolve_configurator_id(zone),
                "disk": plan.disk_gb * 1024,
                "cpu": plan.cpu,
                "ram": plan.ram_mb,
            },
        }

        payload = await self._request("POST", "/servers", json=body)
        server = _server_node(payload)
        return self._to_server(server, fallback_name=name, plan=plan, location=location)

    async def get_server(self, external_id: str) -> VpsServer:
        payload = await self._request("GET", f"/servers/{external_id}")
        return self._to_server(_server_node(payload), fallback_name=external_id)

    async def delete_server(self, external_id: str) -> bool:
        try:
            await self._request("DELETE", f"/servers/{external_id}")
        except VpsApiError as exc:
            if exc.status_code == 404:
                return False
            raise
        return True

    # ---------------------------------------------------------------- helpers

    async def _resolve_os_id(self) -> int | None:
        """Look up the Ubuntu 22.04 image id, tolerating API changes."""
        try:
            payload = await self._request("GET", "/os/servers")
        except VpsApiError:
            return None
        for item in _as_list(payload, "servers_os", "os"):
            if not isinstance(item, dict):
                continue
            if str(item.get("name", "")).lower() == DEFAULT_OS_NAME and str(
                item.get("version", "")
            ).startswith(DEFAULT_OS_VERSION):
                return as_int(item.get("id"))
        return None

    async def _resolve_configurator_id(self, zone: str) -> int | None:
        """Find the configurator serving ``zone``.

        Timeweb rejects a create whose configurator does not cover the chosen
        availability zone, and the ids are not stable across accounts, so they
        have to be read at call time.
        """
        try:
            payload = await self._request("GET", "/configurator/servers")
        except VpsApiError:
            return None
        items = _as_list(payload, "server_configurators", "configurators")
        for item in items:
            if not isinstance(item, dict):
                continue
            zones = item.get("availability_zones") or []
            if zone in {str(z).lower() for z in zones}:
                return as_int(item.get("id"))
        for item in items:
            if isinstance(item, dict) and item.get("id") is not None:
                return as_int(item.get("id"))
        return None

    def _to_server(
        self,
        node: dict[str, Any],
        *,
        fallback_name: str,
        plan: VpsPreset | None = None,
        location: str | None = None,
    ) -> VpsServer:
        zone = str(node.get("availability_zone") or "").lower()
        code = location or next(
            (key for key, value in ZONE_MAP.items() if value == zone), None
        )
        known = find_location(code) if code else None
        configuration = node.get("configuration") or {}
        disk_mb = as_int(configuration.get("disk")) or as_int(node.get("disk"))

        return VpsServer(
            external_id=str(node.get("id") or ""),
            name=str(node.get("name") or fallback_name),
            status=map_status(node.get("status")),
            ip_address=first_ipv4(node.get("networks") or node.get("ip_address")),
            location_code=code,
            location_name=known.name if known else (zone or None),
            preset_name=plan.name if plan else node.get("preset_name"),
            cpu=as_int(configuration.get("cpu")) or as_int(node.get("cpu")),
            ram_mb=as_int(configuration.get("ram")) or as_int(node.get("ram")),
            disk_gb=(disk_mb // 1024 if disk_mb and disk_mb > 1024 else disk_mb),
            raw=node,
        )


def _server_node(payload: Any) -> dict[str, Any]:
    """Unwrap Timeweb's ``{"server": {...}}`` envelope."""
    if isinstance(payload, dict):
        for key in ("server", "data"):
            node = payload.get(key)
            if isinstance(node, dict):
                return node
        return payload
    raise VpsApiError("unexpected Timeweb response shape")


def _as_list(payload: Any, *keys: str) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
        for value in payload.values():
            if isinstance(value, list):
                return value
    return []


__all__ = ["STATUS_MAP", "ZONE_MAP", "TimewebVpsProvider", "map_status"]
