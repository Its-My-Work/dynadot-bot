"""Pydantic request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import (
    Currency,
    DomainStatus,
    Language,
    OrderStatus,
    PaymentProvider,
    PaymentStatus,
    ServerStatus,
    TaskStatus,
    Theme,
    VpsProvider,
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


class ContactUpdate(ContactBase):
    is_default: bool = False


class ContactRead(ORMModel, ContactBase):
    id: str
    is_default: bool
    is_whois_verified: bool


# ----------------------------------------------------------------- search


class DomainSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=253)
    tlds: list[str] | None = None
    category: str | None = None
    offset: int = 0
    limit: int = 15

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
    alternatives: list[str] = Field(default_factory=list)
    ai_suggestions: list[DomainSearchResult] = Field(default_factory=list)
    has_more: bool = False
    total_available_zones: int = 0


class AiSuggestRequest(BaseModel):
    query: str
    tlds: list[str] = Field(default_factory=list)
    candidates: list[str] = Field(default_factory=list)


class AiSuggestResponse(BaseModel):
    query: str
    keywords: list[str] = Field(default_factory=list)
    candidates: list[str] = Field(default_factory=list)
    suggestions: list[DomainSearchResult] = Field(default_factory=list)
    total_checked: int = 0


class CatalogTldItem(BaseModel):
    tld: str
    price_rub: float
    category: str
    is_featured: bool = False


# ---------------------------------------------------------------- domains


class DomainRead(ORMModel):
    id: str
    name: str
    tld: str
    status: DomainStatus
    registered_at: datetime | None = None
    expires_at: datetime | None = None
    auto_renew: bool
    is_locked: bool = True
    privacy_enabled: bool = True
    nameservers: str | None = None


class DnsRecordItem(BaseModel):
    type: str = "A"  # A, AAAA, CNAME, TXT, MX
    host: str = "@"  # @, www, sub
    value: str = ""
    ttl: int = 300
    priority: int | None = None


class DnsRecordsPayload(BaseModel):
    records: list[DnsRecordItem]


class DomainQuickPresetPayload(BaseModel):
    preset_type: str  # vps, telegram, yandex_mail, cloudflare
    target: str | None = None


class DomainPrivacyPayload(BaseModel):
    enabled: bool


class DomainLockPayload(BaseModel):
    locked: bool


class DomainRenewPayload(BaseModel):
    years: int = Field(default=1, ge=1, le=10)


class DomainRenewResponse(BaseModel):
    order_id: str
    amount_rub: float
    payment_url: str | None = None


class DomainDetailsResponse(ORMModel):
    id: str
    name: str
    tld: str
    status: DomainStatus
    registered_at: datetime | None = None
    expires_at: datetime | None = None
    auto_renew: bool = False
    is_locked: bool = True
    privacy_enabled: bool = True
    nameservers: str | None = None
    dns_records: list[DnsRecordItem] = Field(default_factory=list)
    renew_price_rub: float | None = None
    auth_code: str | None = None


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
    retail_price_rub: float | None = None


class TldPricingCreate(BaseModel):
    tld: str
    register_cost_usd: float = Field(default=0.0, ge=0)
    markup_percent: float | None = Field(default=None, ge=0, le=1000)
    fixed_price_rub: float | None = Field(default=None, ge=0)
    is_enabled: bool = True
    is_featured: bool = False


class TldPricingUpdate(BaseModel):
    register_cost_usd: float | None = Field(default=None, ge=0)
    markup_percent: float | None = Field(default=None, ge=0, le=1000)
    fixed_price_rub: float | None = Field(default=None, ge=0)
    is_enabled: bool | None = None
    is_featured: bool | None = None


class TldSyncResponse(BaseModel):
    ok: bool
    synced_count: int
    message: str


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


# ------------------------------------------------------------------ admin auth & settings


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminLoginResponse(BaseModel):
    status: str = "ok"
    username: str
    token: str


class AdminSettingsRead(BaseModel):
    # Financial & Pricing
    usd_to_rub_rate: float
    default_markup_percent: float
    vps_term_days: int = 30

    # Domain Registrar (Dynadot)
    dynadot_base_url: str
    dynadot_configured: bool
    has_dynadot_api_key: bool = False
    has_dynadot_api_secret: bool = False

    # Payments (Platega)
    platega_merchant_id: str | None = None
    platega_base_url: str = "https://app.platega.io"
    platega_payment_method: int = 2
    platega_configured: bool
    has_platega_secret: bool = False

    # Telegram
    support_username: str | None = None
    telegram_configured: bool = False
    has_telegram_bot_token: bool = False

    # VPS & VPN
    vps_provider: str = "mock"
    vps_reality_sni: str = "www.microsoft.com"
    vps_configured: bool = False
    timeweb_configured: bool = False
    has_timeweb_api_token: bool = False
    aeza_configured: bool = False
    has_aeza_api_token: bool = False
    pq_hosting_configured: bool = False
    has_pq_hosting_api_token: bool = False

    # Automation & System
    registration_worker_enabled: bool
    environment: str
    app_version: str = "0.1.0"

    # AI & Suggestions (OmniRoute)
    omniroute_base_url: str = "http://main_omniroute:20128/v1"
    omniroute_model: str = "fast"
    has_omniroute_api_key: bool = False
    ai_configured: bool = False


class AdminSettingsUpdate(BaseModel):
    # Financial & Pricing
    usd_to_rub_rate: float | None = None
    default_markup_percent: float | None = None
    vps_term_days: int | None = None

    # Domain Registrar (Dynadot)
    dynadot_base_url: str | None = None
    dynadot_api_key: str | None = None
    dynadot_api_secret: str | None = None

    # AI & Suggestions (OmniRoute)
    omniroute_base_url: str | None = None
    omniroute_api_key: str | None = None
    omniroute_model: str | None = None

    # Payments (Platega)
    platega_merchant_id: str | None = None
    platega_secret: str | None = None
    platega_base_url: str | None = None
    platega_payment_method: int | None = None

    # Telegram
    telegram_bot_token: str | None = None
    support_username: str | None = None

    # VPS & VPN
    vps_provider: str | None = None
    timeweb_api_token: str | None = None
    aeza_api_token: str | None = None
    pq_hosting_api_token: str | None = None
    vps_reality_sni: str | None = None

    # Automation
    registration_worker_enabled: bool | None = None


class ConnectionTestResponse(BaseModel):
    ok: bool
    provider: str
    message: str
    details: dict[str, Any] | None = None


# ------------------------------------------------------------------ vps / vpn


class VpsLocationRead(BaseModel):
    """One datacentre option in the order form."""

    code: str
    name: str
    country: str
    flag: str = ""
    label: str
    available: bool = True


class VpsPresetRead(BaseModel):
    """One sellable plan with its retail price."""

    code: str
    name: str
    cpu: int
    ram_mb: int
    disk_gb: int
    price_rub: float
    bandwidth_tb: float | None = None
    description: str | None = None


class ServerOrderCreate(BaseModel):
    """Order a VPN server."""

    location: str = Field(min_length=2, max_length=16)
    preset: str = Field(min_length=2, max_length=32)
    name: str | None = Field(default=None, max_length=64)


class ServerRead(ORMModel):
    id: str
    provider: VpsProvider
    name: str
    status: ServerStatus
    external_id: str | None = None
    location_code: str | None = None
    location_name: str | None = None
    ip_address: str | None = None
    preset_name: str | None = None
    cpu: int | None = None
    ram_mb: int | None = None
    disk_gb: int | None = None
    price_rub: float
    vless_uuid: str | None = None
    vless_sni: str | None = None
    vless_link: str | None = None
    expires_at: datetime | None = None
    created_at: datetime


class ServerOrderResponse(BaseModel):
    """A created server order plus the checkout link to pay for it."""

    server: ServerRead
    payment_url: str | None = None
    payment_id: str | None = None
    amount: float
    currency: Currency = Currency.RUB
