"""Operational tables: Dynadot API call log, task queue, admin settings."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ApiCallStatus, TaskStatus


class DynadotApiLog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Every outbound Dynadot call, for the admin panel's API log view."""

    __tablename__ = "dynadot_api_logs"
    __table_args__ = (
        Index("ix_dynadot_api_logs_endpoint_created", "endpoint", "created_at"),
    )

    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    method: Mapped[str] = mapped_column(String(8), default="GET", nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)

    status: Mapped[ApiCallStatus] = mapped_column(
        Enum(ApiCallStatus, native_enum=False, length=32), nullable=False
    )
    http_status: Mapped[int | None] = mapped_column(Integer)
    response_code: Mapped[str | None] = mapped_column(String(32))
    duration_ms: Mapped[float | None] = mapped_column(Float)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Payloads are stored with secrets already redacted by the client layer.
    request_payload: Mapped[str | None] = mapped_column(Text)
    response_payload: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<DynadotApiLog {self.method} {self.endpoint} {self.status.value}>"


class DynadotTask(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Durable record of a queued Dynadot operation.

    Dynadot allows only one concurrent request, so operations are persisted
    here and drained by a single serial worker. Surviving a restart matters:
    a paid-but-unregistered order must not be lost.
    """

    __tablename__ = "dynadot_tasks"
    __table_args__ = (Index("ix_dynadot_tasks_status_priority", "status", "priority"),)

    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    # Lower number = higher priority. Registration must outrank search.
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, native_enum=False, length=32),
        default=TaskStatus.QUEUED,
        nullable=False,
        index=True,
    )

    # Stable idempotency key reused across retries.
    request_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    payload: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)

    order_id: Mapped[str | None] = mapped_column(String(36), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)

    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<DynadotTask {self.operation} {self.status.value}>"


class AppSetting(Base, TimestampMixin):
    """Runtime key/value settings editable from the admin panel."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(String(255))

    def __repr__(self) -> str:
        return f"<AppSetting {self.key}={self.value!r}>"
