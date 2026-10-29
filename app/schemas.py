"""Pydantic request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import DomainStatus, Language, OrderStatus


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


# ------------------------------------------------------------------ users

class UserRead(ORMModel):
    id: str
    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language: Language
    created_at: datetime


class LanguageUpdate(BaseModel):
    language: Language


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
