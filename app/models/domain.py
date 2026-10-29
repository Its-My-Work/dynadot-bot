"""Registered domains and TLD pricing / markup configuration."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import DomainStatus

if TYPE_CHECKING:
    from app.models.user import ContactProfile, User


class Domain(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A domain successfully registered through the service."""

    __tablename__ = "domains"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    contact_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("contact_profiles.id", ondelete="SET NULL")
    )

    name: Mapped[str] = mapped_column(
        String(253), unique=True, index=True, nullable=False
    )
    tld: Mapped[str] = mapped_column(String(63), index=True, nullable=False)

    status: Mapped[DomainStatus] = mapped_column(
        Enum(DomainStatus, native_enum=False, length=32),
        default=DomainStatus.PENDING,
        nullable=False,
    )
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    privacy_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Nameservers are stored denormalised (max 13 per the DNS spec) to avoid a
    # join for the common "show me my NS" read.
    nameservers: Mapped[str | None] = mapped_column(String(1024))

    # Expiry reminder bookkeeping so the bot does not spam the user.
    expiry_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User | None] = relationship(back_populates="domains")
    contact_profile: Mapped[ContactProfile | None] = relationship()

    @property
    def nameserver_list(self) -> list[str]:
        return [ns for ns in (self.nameservers or "").split(",") if ns]

    def __repr__(self) -> str:
        return f"<Domain {self.name} status={self.status.value}>"


class TldPricing(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Per-TLD cost from Dynadot plus the resale markup set in the admin panel."""

    __tablename__ = "tld_pricing"
    __table_args__ = (UniqueConstraint("tld", name="uq_tld_pricing_tld"),)

    tld: Mapped[str] = mapped_column(String(63), index=True, nullable=False)

    # Wholesale cost as quoted by Dynadot, in USD.
    register_cost_usd: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False, default=0
    )
    renew_cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 2))
    transfer_cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 2))

    # Markup override; when NULL the global default_markup_percent applies.
    markup_percent: Mapped[float | None] = mapped_column(Numeric(6, 2))
    # Hard price override in RUB, wins over any computed price when set.
    fixed_price_rub: Mapped[float | None] = mapped_column(Numeric(12, 2))

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    price_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<TldPricing .{self.tld} cost={self.register_cost_usd}>"
