"""Aggregate router for API v1."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    admin,
    contacts,
    domains,
    meta,
    orders,
    payments,
    users,
    vps,
    webhooks,
)

api_router = APIRouter()
api_router.include_router(meta.router)
api_router.include_router(users.router)
api_router.include_router(contacts.router)
api_router.include_router(domains.router)
api_router.include_router(orders.router)
api_router.include_router(payments.router)
api_router.include_router(vps.router)
api_router.include_router(webhooks.router)
api_router.include_router(admin.router)

__all__ = ["api_router"]
