"""Aggregate router for API v1."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import admin, domains, meta, users

api_router = APIRouter()
api_router.include_router(meta.router)
api_router.include_router(users.router)
api_router.include_router(domains.router)
api_router.include_router(admin.router)

__all__ = ["api_router"]
