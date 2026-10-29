"""Purchase orders and payment transactions."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import Currency, OrderStatus, PaymentProvider, PaymentStatus

if TYPE_CHECKING:
    from app.models.domain import Domain
    from app.models.user import ContactProfile, User


class Order(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One domain purchase attempt."""

    __tablename__ = "orders"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    contact_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("contact_profiles.id", ondelete="SET NULL")
    )
    domain_id: Mapped[str | None] = mapped_column(
        ForeignKey("domains.id", ondelete="SET NULL")
    )

    domain_name: Mapped[str] = mapped_column(String(253), index=True, nullable=False)
    tld: Mapped[str] = mapped_column(String(63), nullable=False)
    years: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, native_enum=False, length=32),
        default=OrderStatus.DRAFT,
        nullable=False,
        index=True,
    )

    # Price breakdown is frozen at checkout so later markup changes never
    # alter what the customer already agreed to pay.
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    price_rub: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    markup_percent: Mapped[float] = mapped_column(
        Numeric(6, 2), nullable=False, default=0
    )
    fx_rate: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)

    # Generated once per logical operation and reused on every retry — a fresh
    # id on a register retry risks a double registration and double charge.
    dynadot_request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    dynadot_order_id: Mapped[str | None] = mapped_column(String(64), index=True)

    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="orders")
    contact_profile: Mapped[ContactProfile | None] = relationship()
    domain: Mapped[Domain | None] = relationship()
    payments: Mapped[list[Payment]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Order {self.domain_name} status={self.status.value}>"


class Payment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A payment attempt against an order (Platega, CryptoBot, manual)."""

    __tablename__ = "payments"

    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True, nullable=False
    )

    provider: Mapped[PaymentProvider] = mapped_column(
        Enum(PaymentProvider, native_enum=False, length=32),
        default=PaymentProvider.PLATEGA,
        nullable=False,
    )
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, native_enum=False, length=32),
        default=PaymentStatus.PENDING,
        nullable=False,
        index=True,
    )

    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[Currency] = mapped_column(
        Enum(Currency, native_enum=False, length=8),
        default=Currency.RUB,
        nullable=False,
    )

    # Provider-side identifier; unique to make webhook handling idempotent.
    external_id: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    payment_url: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Raw webhook payload retained for dispute resolution.
    raw_payload: Mapped[str | None] = mapped_column(Text)

    order: Mapped[Order] = relationship(back_populates="payments")

    def __repr__(self) -> str:
        return f"<Payment {self.amount} {self.currency.value} {self.status.value}>"
