"""Aeza VPS adapter (https://my.aeza.net/api).

Aeza's API is JSON-RPC-flavoured REST: every response is wrapped in
``{"data": ...}`` and errors arrive as HTTP 200 with an ``error`` key, so the
success path has to be validated explicitly rather than trusting the status
code. Products are fixed plans (no configurator), and user-data is passed as a
base64 ``cloudInit`` parameter.
"""

from __future__ import annotations

import base64
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
    "started": ServerStatus.ACTIVE,
    "created": ServerStatus.INSTALLING,
    "installing": ServerStatus.INSTALLING,
    "processing": ServerStatus.INSTALLING,
    "pending": ServerStatus.PENDING,
    "queued": ServerStatus.PENDING,
    "suspended": ServerStatus.SUSPENDED,
    "stopped": ServerStatus.SUSPENDED,
    "paused": ServerStatus.SUSPENDED,
    "unpaid": ServerStatus.SUSPENDED,
    "deleted": ServerStatus.DELETED,
    "removed": ServerStatus.DELETED,
    "terminated": ServerStatus.DELETED,
    "error": ServerStatus.FAILED,
    "failed": ServerStatus.FAILED,
}

#: Our location codes -> Aeza datacentre slugs.
LOCATION_MAP = {
    "nl": "nl-ams",
    "pl": "pl-waw",
    "kz": "kz-ala",
    "tr": "tr-ist",
    "fi": "fi-hel",
}

#: Ubuntu 22.04 OS slug used on create.
DEFAULT_OS_SLUG = "ubuntu-22.04"


def map_status(raw: Any) -> ServerStatus:
    if not raw:
        return ServerStatus.PENDING
    return STATUS_MAP.get(str(raw).strip().lower(), ServerStatus.PENDING)


class AezaVpsProvider(HttpVpsProvider):
    """Aeza implementation."""

    provider = VpsProvider.AEZA
    base_url = "https://my.aeza.net/api"

    def _auth_headers(self) -> dict[str, str]:
        # Aeza authenticates with a bare X-API-Key, not a bearer token.
        headers = super()._auth_headers()
        headers.pop("Authorization", None)
        headers["X-API-Key"] = self._token or ""
        return headers

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Request and unwrap, turning an in-body ``error`` into an exception."""
        payload = await self._request(method, path, json=json, params=params)
        if isinstance(payload, dict):
            error = payload.get("error")
            if error:
                message = (
                    error.get("message") if isinstance(error, dict) else str(error)
                )
                raise VpsApiError(f"aeza error: {message}")
            if "data" in payload:
                return payload["data"]
        return payload

    async def list_locations(self) -> list[VpsLocation]:
        try:
            data = await self._call("GET", "/services/products")
        except VpsApiError:
            return list(DEFAULT_LOCATIONS)

        live = set()
        for item in _items(data):
            for key in ("location", "locationCode", "country"):
                value = item.get(key)
                if value:
                    live.add(str(value).lower())
        return [
            VpsLocation(
                code=loc.code,
                name=loc.name,
                country=loc.country,
                flag=loc.flag,
                available=not live
                or LOCATION_MAP.get(loc.code, "") in live
                or loc.code in live,
            )
            for loc in DEFAULT_LOCATIONS
        ]

    async def list_presets(self) -> list[VpsPreset]:
        """Return retail tiers, annotated with the Aeza product id to order.

        The upstream product list is matched on RAM because that is the only
        field Aeza reports consistently across its plan generations; when the
        lookup fails the tier is still sellable, it just has no ``external_id``
        and create falls back to the configured default product.
        """
        try:
            data = await self._call("GET", "/services/products")
        except VpsApiError:
            return list(DEFAULT_PRESETS)

        by_ram: dict[int, str] = {}
        for item in _items(data):
            ram = as_int(item.get("ram") or (item.get("params") or {}).get("ram"))
            pid = item.get("id")
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
        slug = LOCATION_MAP.get(location.lower())
        if slug is None:
            raise VpsApiError(f"unsupported location {location!r}")

        product_id = next(
            (
                p.external_id
                for p in await self.list_presets()
                if p.code == plan.code and p.external_id
            ),
            None,
        )

        body = {
            "name": name,
            "productId": as_int(product_id) if product_id else None,
            "location": slug,
            "os": DEFAULT_OS_SLUG,
            "term": "month",
            "count": 1,
            "autoProlong": False,
            # Aeza takes user-data base64-encoded; a raw YAML body is rejected
            # as an invalid parameter.
            "cloudInit": base64.b64encode(cloud_init.encode("utf-8")).decode("ascii"),
        }

        data = await self._call("POST", "/services/orders", json=body)
        node = _service_node(data)
        return self._to_server(node, fallback_name=name, plan=plan, location=location)

    async def get_server(self, external_id: str) -> VpsServer:
        data = await self._call("GET", f"/services/{external_id}")
        return self._to_server(_service_node(data), fallback_name=external_id)

    async def delete_server(self, external_id: str) -> bool:
        try:
            await self._call("DELETE", f"/services/{external_id}")
        except VpsApiError as exc:
            if exc.status_code == 404 or "not found" in str(exc).lower():
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
        params = node.get("params") or {}
        slug = str(node.get("location") or node.get("locationCode") or "").lower()
        code = location or next(
            (key for key, value in LOCATION_MAP.items() if value == slug), None
        )
        known = find_location(code) if code else None

        return VpsServer(
            external_id=str(node.get("id") or ""),
            name=str(node.get("name") or fallback_name),
            status=map_status(node.get("status") or node.get("state")),
            ip_address=first_ipv4(
                node.get("ip") or node.get("ips") or node.get("addresses")
            ),
            location_code=code,
            location_name=known.name if known else (slug or None),
            preset_name=plan.name
            if plan
            else (node.get("productName") or params.get("name")),
            cpu=as_int(node.get("cpu") or params.get("cpu")),
            ram_mb=as_int(node.get("ram") or params.get("ram")),
            disk_gb=_disk_gb(node, params),
            raw=node,
        )


def _disk_gb(node: dict[str, Any], params: dict[str, Any]) -> int | None:
    raw = node.get("disk") or params.get("disk")
    value = as_int(raw)
    if value is None:
        return None
    # Aeza reports disk in MB on newer plans and GB on legacy ones.
    return value // 1024 if value >= 1024 else value


def _items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("items", "products", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [i for i in value if isinstance(i, dict)]
        return [data] if data else []
    if isinstance(data, list):
        return [i for i in data if isinstance(i, dict)]
    return []


def _service_node(data: Any) -> dict[str, Any]:
    """Pull the single service object out of an order/get response."""
    items = _items(data)
    if not items:
        raise VpsApiError("unexpected Aeza response shape")
    node = items[0]
    # An order response nests the created service one level deeper.
    for key in ("service", "items"):
        inner = node.get(key)
        if isinstance(inner, dict):
            return inner
        if isinstance(inner, list) and inner and isinstance(inner[0], dict):
            return inner[0]
    return node


__all__ = ["LOCATION_MAP", "STATUS_MAP", "AezaVpsProvider", "map_status"]
