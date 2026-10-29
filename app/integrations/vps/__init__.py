"""VPS/VPN provisioning integrations."""

from __future__ import annotations

from app.integrations.vps.aeza import AezaVpsProvider
from app.integrations.vps.base import (
    BaseVpsProvider,
    VpsApiError,
    VpsConfigError,
    VpsLocation,
    VpsPreset,
    VpsProviderError,
    VpsServer,
    VpsTransportError,
)
from app.integrations.vps.catalog import (
    DEFAULT_LOCATIONS,
    DEFAULT_PRESETS,
    SERVER_TERM_DAYS,
    find_location,
    find_preset,
)
from app.integrations.vps.mock import MockVpsProvider
from app.integrations.vps.pqhosting import PqHostingVpsProvider
from app.integrations.vps.timeweb import TimewebVpsProvider
from app.models.enums import VpsProvider

PROVIDER_CLASSES: dict[VpsProvider, type[BaseVpsProvider]] = {
    VpsProvider.TIMEWEB: TimewebVpsProvider,
    VpsProvider.AEZA: AezaVpsProvider,
    VpsProvider.PQ_HOSTING: PqHostingVpsProvider,
    VpsProvider.MOCK: MockVpsProvider,
}


def build_provider(settings) -> BaseVpsProvider:
    """Build the configured provider, degrading to the mock without keys.

    Falling back instead of raising is deliberate: the catalogue endpoints and
    the whole checkout UI must keep working on a deploy where VPS keys have not
    been issued yet, and a mock-provisioned row is clearly marked as such in
    the database.
    """
    chosen = settings.vps_provider
    if isinstance(chosen, str):
        chosen = VpsProvider(chosen)

    token = settings.vps_api_token_for(chosen)
    if chosen is VpsProvider.MOCK or not token:
        return MockVpsProvider()

    cls = PROVIDER_CLASSES[chosen]
    return cls(  # type: ignore[call-arg]
        api_token=token,
        timeout=settings.vps_timeout_seconds,
    )


__all__ = [
    "DEFAULT_LOCATIONS",
    "DEFAULT_PRESETS",
    "PROVIDER_CLASSES",
    "SERVER_TERM_DAYS",
    "AezaVpsProvider",
    "BaseVpsProvider",
    "MockVpsProvider",
    "PqHostingVpsProvider",
    "TimewebVpsProvider",
    "VpsApiError",
    "VpsConfigError",
    "VpsLocation",
    "VpsPreset",
    "VpsProviderError",
    "VpsServer",
    "VpsTransportError",
    "build_provider",
    "find_location",
    "find_preset",
]
