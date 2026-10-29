"""Rented VPN servers (VPS + Xray Reality)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ServerStatus, VpsProvider

if TYPE_CHECKING:
    from app.models.user import User


class ServerInstance(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One VPS rented on a customer's behalf, with its Reality credentials.

    The VLESS secrets are generated before the VM exists (see
    ``app.integrations.vps.xray``) and stored here, so the customer's
    connection string is available immediately and survives a provider-side
    rebuild — we re-apply the same cloud-init instead of re-keying every
    client.

    ``external_id`` is nullable because the row is written *before* the create
    call: if provisioning dies mid-flight we still have a record of what was
    paid for, in FAILED status, rather than an orphaned charge.
    """

    __tablename__ = "server_instances"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    provider: Mapped[VpsProvider] = mapped_column(
        Enum(VpsProvider, native_enum=False, length=32),
        default=VpsProvider.MOCK,
        nullable=False,
        index=True,
    )
    external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    location_code: Mapped[str | None] = mapped_column(String(16))
    location_name: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(45), index=True)

    status: Mapped[ServerStatus] = mapped_column(
        Enum(ServerStatus, native_enum=False, length=32),
        default=ServerStatus.PENDING,
        nullable=False,
        index=True,
    )

    preset_name: Mapped[str | None] = mapped_column(String(64))
    cpu: Mapped[int | None] = mapped_column(Integer)
    ram_mb: Mapped[int | None] = mapped_column(Integer)
    disk_gb: Mapped[int | None] = mapped_column(Integer)
    price_rub: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)

    # --- Xray Reality key material ----------------------------------------
    vless_uuid: Mapped[str | None] = mapped_column(String(64))
    vless_public_key: Mapped[str | None] = mapped_column(String(128))
    vless_short_id: Mapped[str | None] = mapped_column(String(32))
    vless_sni: Mapped[str | None] = mapped_column(String(253))
    # The ready-to-paste share link. Denormalised on purpose: it is what the
    # TMA shows and the bot sends, and rebuilding it on every read would mean
    # re-deriving it from five columns in three places.
    vless_link: Mapped[str | None] = mapped_column(Text)

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )

    user: Mapped[User] = relationship(back_populates="servers")

    __table_args__ = (
        Index("ix_server_instances_user_status", "user_id", "status"),
    )

    @property
    def is_live(self) -> bool:
        """True when the customer should still see this server in their list."""
        return self.status in {
            ServerStatus.PENDING,
            ServerStatus.INSTALLING,
            ServerStatus.ACTIVE,
            ServerStatus.SUSPENDED,
        }

    def __repr__(self) -> str:
        return f"<ServerInstance {self.name} status={self.status.value}>"


__all__ = ["ServerInstance"]
