"""Enumerations shared across ORM models and API schemas."""

from __future__ import annotations

import enum


class Language(str, enum.Enum):
    RU = "ru"
    EN = "en"


class Theme(str, enum.Enum):
    """Commercial design themes selectable in the TMA and admin panel.

    Stored on the user profile so the choice follows the account across
    devices; the frontend also mirrors it into ``localStorage`` so the very
    first paint is already in the right palette (no flash of the default).
    """

    ONYX_NEON = "onyx-neon"
    VERCEL_MONO = "vercel-mono"
    LINEAR_INDIGO = "linear-indigo"
    SOLANA_CYBER = "solana-cyber"
    NORDIC_MIST = "nordic-mist"


DEFAULT_THEME = Theme.ONYX_NEON


class UserRole(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"


class OrderStatus(str, enum.Enum):
    """Lifecycle of a domain purchase.

    REFUND_REQUIRED exists because a domain can be registered by someone else
    between taking the customer's payment and calling Dynadot's register
    endpoint — that money must never be silently kept.
    """

    DRAFT = "draft"
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    REGISTERING = "registering"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUND_REQUIRED = "refund_required"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"
    EXPIRED = "expired"


class PaymentProvider(str, enum.Enum):
    PLATEGA = "platega"
    CRYPTOBOT = "cryptobot"
    MANUAL = "manual"


class DomainStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CLIENT_HOLD = "client_hold"
    TRANSFERRED_AWAY = "transferred_away"
    PENDING = "pending"


class Currency(str, enum.Enum):
    RUB = "RUB"
    USD = "USD"


class ApiCallStatus(str, enum.Enum):
    SUCCESS = "success"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"


class VpsProvider(str, enum.Enum):
    """Upstream cloud a server was rented from.

    ``MOCK`` is a first-class value, not a test artefact: when no API keys are
    configured the API still has to serve the catalogue and the order flow, and
    a persisted row needs to record that its server is not real.
    """

    TIMEWEB = "timeweb"
    AEZA = "aeza"
    PQ_HOSTING = "pq_hosting"
    MOCK = "mock"


class ServerStatus(str, enum.Enum):
    """Lifecycle of a rented VPN server.

    INSTALLING covers the window between the provider reporting the VM as
    created and cloud-init finishing the Xray install — the VLESS link already
    exists at that point but will not connect yet.
    """

    PENDING = "pending"
    INSTALLING = "installing"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"
    FAILED = "failed"


class TaskStatus(str, enum.Enum):
    """State of a queued outbound Dynadot operation."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
