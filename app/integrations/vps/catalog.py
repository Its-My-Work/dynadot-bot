"""The retail catalogue: what we sell and for how much.

Upstream plan prices are wholesale and change without notice; these are the
numbers the customer is quoted. Keeping them in one module means a price change
is one edit, and every provider adapter maps its own plan ids onto these three
tiers instead of inventing its own pricing.
"""

from __future__ import annotations

from app.integrations.vps.base import VpsLocation, VpsPreset

#: Locations we offer. Ordered by how well they perform from RU networks.
DEFAULT_LOCATIONS: tuple[VpsLocation, ...] = (
    VpsLocation(code="nl", name="Amsterdam", country="NL", flag="🇳🇱"),
    VpsLocation(code="pl", name="Warsaw", country="PL", flag="🇵🇱"),
    VpsLocation(code="kz", name="Almaty", country="KZ", flag="🇰🇿"),
    VpsLocation(code="tr", name="Istanbul", country="TR", flag="🇹🇷"),
    VpsLocation(code="fi", name="Helsinki", country="FI", flag="🇫🇮"),
)

#: Retail plans. One vCPU is enough for Reality at gigabit line rate, so the
#: tiers mostly differ in RAM/disk and in how much traffic they include.
DEFAULT_PRESETS: tuple[VpsPreset, ...] = (
    VpsPreset(
        code="start",
        name="Start",
        cpu=1,
        ram_mb=1024,
        disk_gb=15,
        price_rub=790.0,
        bandwidth_tb=1.0,
        description="1 vCPU · 1 GB RAM · 15 GB NVMe — 1–3 devices",
    ),
    VpsPreset(
        code="standard",
        name="Standard",
        cpu=2,
        ram_mb=2048,
        disk_gb=30,
        price_rub=1290.0,
        bandwidth_tb=3.0,
        description="2 vCPU · 2 GB RAM · 30 GB NVMe — family use",
    ),
    VpsPreset(
        code="pro",
        name="Pro",
        cpu=4,
        ram_mb=4096,
        disk_gb=60,
        price_rub=2490.0,
        bandwidth_tb=10.0,
        description="4 vCPU · 4 GB RAM · 60 GB NVMe — teams, heavy traffic",
    ),
)

PRESETS_BY_CODE = {preset.code: preset for preset in DEFAULT_PRESETS}
LOCATIONS_BY_CODE = {location.code: location for location in DEFAULT_LOCATIONS}

#: How long a paid server runs before it needs renewing.
SERVER_TERM_DAYS = 30


def find_preset(code: str) -> VpsPreset | None:
    return PRESETS_BY_CODE.get(code.strip().lower()) if code else None


def find_location(code: str) -> VpsLocation | None:
    return LOCATIONS_BY_CODE.get(code.strip().lower()) if code else None


__all__ = [
    "DEFAULT_LOCATIONS",
    "DEFAULT_PRESETS",
    "LOCATIONS_BY_CODE",
    "PRESETS_BY_CODE",
    "SERVER_TERM_DAYS",
    "find_location",
    "find_preset",
]
