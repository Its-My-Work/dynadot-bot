"""Provider-agnostic VPS contracts.

Every provider returns wildly different JSON (Timeweb nests under ``server``,
Aeza under ``data``, PQ.Hosting returns a flat object) and uses different words
for the same state. Normalising all of it into these dataclasses here keeps the
API routes and the ORM free of per-provider branching, which is what makes
adding a fourth upstream a single-file change.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import ServerStatus, VpsProvider


@dataclass(frozen=True, slots=True)
class VpsLocation:
    """One datacentre a customer can pick."""

    code: str
    name: str
    country: str
    flag: str = ""
    available: bool = True

    @property
    def label(self) -> str:
        """Display string for the TMA picker, e.g. ``🇳🇱 Amsterdam``."""
        return f"{self.flag} {self.name}".strip()


@dataclass(frozen=True, slots=True)
class VpsPreset:
    """A sellable plan: hardware plus the price we charge for it."""

    code: str
    name: str
    cpu: int
    ram_mb: int
    disk_gb: int
    price_rub: float
    #: Provider-side plan/preset identifier passed back on create.
    external_id: str | None = None
    bandwidth_tb: float | None = None
    description: str | None = None


@dataclass(slots=True)
class VpsServer:
    """A server as it exists upstream, normalised."""

    external_id: str
    name: str
    status: ServerStatus
    ip_address: str | None = None
    location_code: str | None = None
    location_name: str | None = None
    preset_name: str | None = None
    cpu: int | None = None
    ram_mb: int | None = None
    disk_gb: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class VpsProviderError(RuntimeError):
    """Any failure talking to an upstream cloud."""


class VpsConfigError(VpsProviderError):
    """The provider is missing credentials."""


class VpsTransportError(VpsProviderError):
    """Network-level failure (timeout, DNS, connection reset)."""


class VpsApiError(VpsProviderError):
    """The provider answered, but with an error."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class BaseVpsProvider(abc.ABC):
    """Interface every VPS upstream implements."""

    provider: VpsProvider

    @abc.abstractmethod
    async def list_locations(self) -> list[VpsLocation]:
        """Return the datacentres available for new orders."""

    @abc.abstractmethod
    async def list_presets(self) -> list[VpsPreset]:
        """Return the sellable plans."""

    @abc.abstractmethod
    async def create_server(
        self,
        location: str,
        preset: str,
        cloud_init: str,
        name: str,
    ) -> VpsServer:
        """Provision a server, passing ``cloud_init`` as user-data."""

    @abc.abstractmethod
    async def get_server(self, external_id: str) -> VpsServer:
        """Fetch current upstream state for one server."""

    @abc.abstractmethod
    async def delete_server(self, external_id: str) -> bool:
        """Destroy a server. Returns False when it was already gone."""

    async def aclose(self) -> None:
        """Release the HTTP client, if the implementation holds one.

        Not abstract: the in-memory providers hold nothing to close, and
        forcing them to implement an empty override buys nothing.
        """
        return None

    async def __aenter__(self) -> BaseVpsProvider:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()


__all__ = [
    "BaseVpsProvider",
    "VpsApiError",
    "VpsConfigError",
    "VpsLocation",
    "VpsPreset",
    "VpsProviderError",
    "VpsServer",
    "VpsTransportError",
]
