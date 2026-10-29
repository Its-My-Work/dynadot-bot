"""User accounts and reusable domain-owner contact profiles."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import DEFAULT_THEME, Language, Theme, UserRole

if TYPE_CHECKING:
    from app.models.domain import Domain
    from app.models.order import Order


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A Telegram user of the service."""

    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True, nullable=False
    )
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))

    # Persisted language preference — the TMA switches it in one tap.
    language: Mapped[Language] = mapped_column(
        Enum(Language, native_enum=False, length=8),
        default=Language.RU,
        nullable=False,
    )
    # Persisted design theme — mirrored into localStorage by the frontend so
    # the first paint already uses the right palette.
    theme: Mapped[Theme] = mapped_column(
        Enum(Theme, native_enum=False, length=32),
        default=DEFAULT_THEME,
        server_default=DEFAULT_THEME.name,
        nullable=False,
    )
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False, length=16),
        default=UserRole.USER,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    contacts: Mapped[list[ContactProfile]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    orders: Mapped[list[Order]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    domains: Mapped[list[Domain]] = relationship(back_populates="user")

    @property
    def display_name(self) -> str:
        if self.username:
            return f"@{self.username}"
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) or f"id{self.telegram_id}"

    def __repr__(self) -> str:
        return f"<User telegram_id={self.telegram_id} lang={self.language.value}>"


class ContactProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """WHOIS registrant data, saved so repeat purchases need no re-entry.

    `dynadot_contact_id` caches the id returned by Dynadot so we create the
    contact once instead of per order.
    """

    __tablename__ = "contact_profiles"
    __table_args__ = (Index("ix_contact_profiles_user_default", "user_id", "is_default"),)

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dynadot_contact_id: Mapped[str | None] = mapped_column(String(64), index=True)

    first_name: Mapped[str] = mapped_column(String(128), nullable=False)
    last_name: Mapped[str] = mapped_column(String(128), nullable=False)
    organization: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone_country_code: Mapped[str] = mapped_column(String(8), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False)

    address_line1: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str | None] = mapped_column(String(128))
    zip_code: Mapped[str] = mapped_column(String(32), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)

    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Unverified WHOIS contacts get domains put on clientHold by the registry.
    is_whois_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="contacts")

    def __repr__(self) -> str:
        return f"<ContactProfile {self.first_name} {self.last_name} ({self.email})>"
