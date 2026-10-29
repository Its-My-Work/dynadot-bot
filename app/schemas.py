"""Pydantic request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import (
    Currency,
    DomainStatus,
    Language,
    OrderStatus,
    PaymentProvider,
    PaymentStatus,
    TaskStatus,
    Theme,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ meta

class HealthResponse(BaseModel):
    status: str = "ok"
    environment: str
    version: str
    database: str
    dynadot_configured: bool


class LanguagesResponse(BaseModel):
    default: str
    supported: list[str]


class ThemeInfo(BaseModel):
    """One selectable design theme, described for the UI picker."""

    id: Theme
    name: str
    accent: str
    scheme: str


class ThemesResponse(BaseModel):
    default: Theme
    themes: list[ThemeInfo]


# ------------------------------------------------------------------ users

class UserRead(ORMModel):
    id: str
    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language: Language
    theme: Theme = Theme.ONYX_NEON
    created_at: datetime


class LanguageUpdate(BaseModel):
    language: Language


class ThemeUpdate(BaseModel):
    theme: Theme


# --------------------------------------------------------------- contacts

class ContactBase(BaseModel):
    first_name: str = Field(min_length=1, max_length=128)
    last_name: str = Field(min_length=1, max_length=128)
    organization: str | None = Field(default=None, max_length=255)
    email: EmailStr
    phone_country_code: str = Field(min_length=1, max_length=8)
    phone_number: str = Field(min_length=4, max_length=32)
    address_line1: str = Field(min_length=1, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    city: str = Field(min_length=1, max_length=128)
    state: str | None = Field(default=None, max_length=128)
    zip_code: str = Field(min_length=1, max_length=32)
    country: str = Field(min_length=2, max_length=2)

    @field_validator("country")
    @classmethod
    def _upper_country(cls, value: str) -> str:
        return value.upper()


class ContactCreate(ContactBase):
    is_default: bool = False


class ContactRead(ORMModel, ContactBase):
    id: str
    is_default: bool
    is_whois_verified: bool


# ----------------------------------------------------------------- search

class DomainSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=253)
    tlds: list[str] | None = None

    @field_validator("query")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return value.strip().lower().lstrip(".")


class DomainSearchResult(BaseModel):
    domain: str
    tld: str
    available: bool
    is_premium: bool = False
    price_rub: float | None = None
    price_usd: float | None = None
    message: str | None = None


class DomainSearchResponse(BaseModel):
    query: str
    results: list[DomainSearchResult]


# ---------------------------------------------------------------- domains

class DomainRead(ORMModel):
    id: str
    name: str
    tld: str
    status: DomainStatus
    registered_at: datetime | None = None
    expires_at: datetime | None = None
    auto_renew: bool
    nameservers: str | None = None


class NameserverUpdate(BaseModel):
    nameservers: list[str] = Field(min_length=1, max_length=13)


# ----------------------------------------------------------------- orders

class OrderCreate(BaseModel):
    domain_name: str = Field(min_length=3, max_length=253)
    years: int = Field(default=1, ge=1, le=10)
    contact_profile_id: str | None = None


class OrderRead(ORMModel):
    id: str
    domain_name: str
    tld: str
    years: int
    status: OrderStatus
    price_rub: float
    created_at: datetime
    error_message: str | None = None


# ------------------------------------------------------------------ admin

class TldPricingRead(ORMModel):
    id: str
    tld: str
    register_cost_usd: float
    markup_percent: float | None = None
    fixed_price_rub: float | None = None
    is_enabled: bool
    is_featured: bool


class TldPricingUpdate(BaseModel):
    markup_percent: float | None = Field(default=None, ge=0, le=1000)
    fixed_price_rub: float | None = Field(default=None, ge=0)
    is_enabled: bool | None = None
    is_featured: bool | None = None


class ApiLogRead(ORMModel):
    id: str
    endpoint: str
    method: str
    status: str
    http_status: int | None = None
    duration_ms: float | None = None
    attempt: int
    created_at: datetime


class TaskRead(ORMModel):
    """A queued Dynadot operation, as shown in the admin queue view."""

    id: str
    operation: str
    status: TaskStatus
    priority: int
    order_id: str | None = None
    attempts: int
    max_attempts: int
    last_error: str | None = None
    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class AdminOrderRead(ORMModel):
    """Richer order view for operators — includes the money breakdown."""

    id: str
    user_id: str
    domain_name: str
    tld: str
    years: int
    status: OrderStatus
    cost_usd: float
    price_rub: float
    markup_percent: float
    error_code: str | None = None
    error_message: str | None = None
    registered_at: datetime | None = None
    created_at: datetime


class AdminStats(BaseModel):
    """Headline numbers for the admin dashboard."""

    users: int
    orders_total: int
    orders_completed: int
    orders_awaiting_payment: int
    refunds_pending: int
    domains_active: int
    revenue_rub: float
    tasks_queued: int
    tasks_dead_letter: int


class RefundResolve(BaseModel):
    """Mark a REFUND_REQUIRED order as money-returned."""

    note: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------- payments

class CheckoutCreate(BaseModel):
    """Start a payment for an existing order."""

    order_id: str = Field(min_length=1, max_length=64)


class PaymentRead(ORMModel):
    id: str
    order_id: str
    provider: PaymentProvider
    status: PaymentStatus
    amount: float
    currency: Currency
    external_id: str | None = None
    payment_url: str | None = None
    created_at: datetime
    paid_at: datetime | None = None


class CheckoutResponse(BaseModel):
    """What the TMA needs to redirect the customer to the gateway."""

    payment_id: str
    order_id: str
    status: PaymentStatus
    amount: float
    currency: Currency
    payment_url: str | None = None
    message: str | None = None


class WebhookAck(BaseModel):
    """Acknowledgement body; gateways only care about the HTTP status."""

    ok: bool = True
    # False when the event was a duplicate or an illegal transition.
    applied: bool = False
    detail: str | None = None
