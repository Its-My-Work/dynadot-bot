"""Security helpers: password hashing, single-use codes, and auth tokens."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets


def hash_password(password: str) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with 100,000 iterations and 128-bit salt."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return f"pbkdf2:sha256:100000${salt.hex()}${dk.hex()}"


def verify_password(password: str, hashed: str | None) -> bool:
    """Verify raw password against stored PBKDF2 hash using constant-time comparison."""
    if not hashed or not password:
        return False
    try:
        parts = hashed.split("$")
        if len(parts) != 3:
            return False
        salt = bytes.fromhex(parts[1])
        expected_hash = parts[2]
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
        return hmac.compare_digest(dk.hex(), expected_hash)
    except Exception:
        return False


def hash_code(code: str) -> str:
    """Hash a short 6-digit Telegram login code for storage."""
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def generate_login_code() -> str:
    """Generate a random 6-digit numeric login code."""
    return f"{secrets.randbelow(900000) + 100000}"


def generate_auth_token(user_id: str) -> str:
    """Generate an opaque session token for client authentication."""
    random_part = secrets.token_urlsafe(32)
    return f"dyn_{user_id}_{random_part}"
