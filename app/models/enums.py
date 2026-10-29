"""Enumerations shared across ORM models and API schemas."""

from __future__ import annotations

import enum


class Language(str, enum.Enum):
    RU = "ru"
    EN = "en"


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


class TaskStatus(str, enum.Enum):
    """State of a queued outbound Dynadot operation."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
