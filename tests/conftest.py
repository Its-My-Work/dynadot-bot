"""Shared pytest fixtures and test environment setup."""

from __future__ import annotations

import os

# Must be set before app.core.config is imported anywhere.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DYNADOT_API_KEY", "test-key")
os.environ.setdefault("DYNADOT_API_SECRET", "test-secret")
os.environ.setdefault("ENVIRONMENT", "local")
