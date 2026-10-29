"""ORM model registry.

Importing this package registers every model on ``Base.metadata`` — Alembic
autogenerate and ``create_all`` both depend on that side effect.
"""

from __future__ import annotations

from app.db.base import Base
from app.models.audit import AppSetting, DynadotApiLog, DynadotTask
from app.models.domain import Domain, TldPricing
from app.models.enums import (
    ApiCallStatus,
    Currency,
    DomainStatus,
    Language,
    OrderStatus,
    PaymentProvider,
    PaymentStatus,
    TaskStatus,
    UserRole,
)
from app.models.order import Order, Payment
from app.models.user import ContactProfile, User

__all__ = [
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
    "TaskStatus",
    "TldPricing",
    "User",
    "UserRole",
]
