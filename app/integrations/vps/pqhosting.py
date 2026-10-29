"""PQ.Hosting VPS adapter.

PQ.Hosting exposes the same vendor API as Aeza's older generation (flat JSON,
bearer token, fixed plans) but on its own host and with ``user_data`` passed as
plain YAML. It is the fallback upstream for TR/KZ capacity, which Timeweb
regularly runs out of.
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

STATUS_MAP = {
    "active": ServerStatus.ACTIVE,
    "running": ServerStatus.ACTIVE,
    "online": ServerStatus.ACTIVE,
    "install": ServerStatus.INSTALLING,
    "installing": ServerStatus.INSTALLING,
    "building": ServerStatus.INSTALLING,
    "pending": ServerStatus.PENDING,
    "new": ServerStatus.PENDING,
    "suspended": ServerStatus.SUSPENDED,
    "offline": ServerStatus.SUSPENDED,
    "stopped": ServerStatus.SUSPENDED,
    "deleted": ServerStatus.DELETED,
    "destroyed": ServerStatus.DELETED,
    "error": ServerStatus.FAILED,
    "failed": ServerStatus.FAILED,
}

#: Our codes -> PQ.Hosting datacentre ids.
LOCATION_MAP = {
    "nl": "amsterdam",
    "pl": "warsaw",
    "kz": "almaty",
    "tr": "istanbul",
    "fi": "helsinki",
}

DEFAULT_OS_SLUG = "ubuntu-24.04"


def map_status(raw: Any) -> ServerStatus:
    if not raw:
        return ServerStatus.PENDING
    return STATUS_MAP.get(str(raw).strip().lower(), ServerStatus.PENDING)


class PqHostingVpsProvider(HttpVpsProvider):
    """PQ.Hosting implementation."""

    provider = VpsProvider.PQ_HOSTING
    base_url = "https://pq.hosting/api/v1"

    async def list_locations(self) -> list[VpsLocation]:
        try:
            payload = await self._request("GET", "/locations")
        except VpsApiError:
            return list(DEFAULT_LOCATIONS)

        live = {
            str(item.get("id") or item.get("slug") or item.get("code") or "").lower()
            for item in _items(payload)
        }
        return [
            VpsLocation(
                code=loc.code,
                name=loc.name,
                country=loc.country,
                flag=loc.flag,
                available=not live or LOCATION_MAP.get(loc.code, "") in live,
            )
            for loc in DEFAULT_LOCATIONS
        ]

    async def list_presets(self) -> list[VpsPreset]:
        """Retail tiers annotated with the upstream plan id when matchable."""
        try:
            payload = await self._request("GET", "/plans")
        except VpsApiError:
            return list(DEFAULT_PRESETS)

        by_ram: dict[int, str] = {}
        for item in _items(payload):
            ram = as_int(item.get("ram") or item.get("memory"))
            pid = item.get("id") or item.get("slug")
            if ram and pid is not None and ram not in by_ram:
                by_ram[ram] = str(pid)

        return [
            VpsPreset(
                code=p.code,
                name=p.name,
                cpu=p.cpu,
                ram_mb=p.ram_mb,
                disk_gb=p.disk_gb,
                price_rub=p.price_rub,
                external_id=by_ram.get(p.ram_mb),
                bandwidth_tb=p.bandwidth_tb,
                description=p.description,
            )
            for p in DEFAULT_PRESETS
        ]

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
        dc = LOCATION_MAP.get(location.lower())
        if dc is None:
            raise VpsApiError(f"unsupported location {location!r}")

        plan_id = next(
            (
                p.external_id
                for p in await self.list_presets()
                if p.code == plan.code and p.external_id
            ),
            None,
        )

        body = {
            "hostname": name,
            "label": name,
            "location": dc,
            "plan": plan_id or plan.code,
            "os": DEFAULT_OS_SLUG,
            "period": 1,
            "backups": False,
            # Plain YAML here: PQ.Hosting base64-decodes nothing and would
            # hand the encoded blob to cloud-init verbatim.
            "user_data": cloud_init,
        }

        payload = await self._request("POST", "/vps", json=body)
        node = _node(payload)
        return self._to_server(node, fallback_name=name, plan=plan, location=location)

    async def get_server(self, external_id: str) -> VpsServer:
        payload = await self._request("GET", f"/vps/{external_id}")
        return self._to_server(_node(payload), fallback_name=external_id)

    async def delete_server(self, external_id: str) -> bool:
        try:
            await self._request("DELETE", f"/vps/{external_id}")
        except VpsApiError as exc:
            if exc.status_code == 404:
                return False
            raise
        return True

    # ---------------------------------------------------------------- mapping

    def _to_server(
        self,
        node: dict[str, Any],
        *,
        fallback_name: str,
        plan: VpsPreset | None = None,
        location: str | None = None,
    ) -> VpsServer:
        dc = str(node.get("location") or node.get("datacenter") or "").lower()
        code = location or next(
            (key for key, value in LOCATION_MAP.items() if value == dc), None
        )
        known = find_location(code) if code else None
        ram = as_int(node.get("ram") or node.get("memory"))
        disk = as_int(node.get("disk") or node.get("disk_gb"))

        return VpsServer(
            external_id=str(node.get("id") or node.get("uuid") or ""),
            name=str(node.get("hostname") or node.get("label") or fallback_name),
            status=map_status(node.get("status") or node.get("state")),
            ip_address=first_ipv4(
                node.get("ipv4") or node.get("ip") or node.get("ips")
            ),
            location_code=code,
            location_name=known.name if known else (dc or None),
            preset_name=plan.name if plan else node.get("plan"),
            cpu=as_int(node.get("cpu") or node.get("vcpu")),
            ram_mb=ram,
            disk_gb=(disk // 1024 if disk and disk >= 1024 else disk),
            raw=node,
        )


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [i for i in payload if isinstance(i, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "plans", "locations", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return [i for i in value if isinstance(i, dict)]
    return []


def _node(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        for key in ("vps", "server", "data", "result"):
            inner = payload.get(key)
            if isinstance(inner, dict):
                return inner
            if isinstance(inner, list) and inner and isinstance(inner[0], dict):
                return inner[0]
        return payload
    raise VpsApiError("unexpected PQ.Hosting response shape")


__all__ = ["LOCATION_MAP", "STATUS_MAP", "PqHostingVpsProvider", "map_status"]
