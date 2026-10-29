"""ORM model registry.

Importing this package registers every model on ``Base.metadata`` — Alembic
autogenerate and ``create_all`` both depend on that side effect.
"""

from __future__ import annotations

from app.db.base import Base
from app.models.audit import AppSetting, DynadotApiLog, DynadotTask
from app.models.domain import Domain, TldPricing
from app.models.enums import (
    DEFAULT_THEME,
    ApiCallStatus,
    Currency,
    DomainStatus,
    Language,
    OrderStatus,
    PaymentProvider,
    PaymentStatus,
    ServerStatus,
    TaskStatus,
    Theme,
    UserRole,
    VpsProvider,
)
from app.models.order import Order, Payment
from app.models.user import ContactProfile, User
from app.models.vps import ServerInstance

__all__ = [
    "DEFAULT_THEME",
    "ApiCallStatus",
    "AppSetting",
    "Base",
    "ContactProfile",
    "Currency",
    "Domain",
    "DomainStatus",
    "DynadotApiLog",
    "DynadotTask",
    "Language",
    "Order",
    "OrderStatus",
    "Payment",
    "PaymentProvider",
    "PaymentStatus",
    "ServerInstance",
    "ServerStatus",
    "TaskStatus",
    "Theme",
    "TldPricing",
    "User",
    "UserRole",
    "VpsProvider",
]
