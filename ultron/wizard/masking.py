"""Mask sensitive values for display in the configuration wizard."""

from __future__ import annotations


def is_sensitive_key(key: str) -> bool:
    u = key.upper()
    if "TOKEN" in u or "SECRET" in u or "PASSWORD" in u:
        return True
    if u.endswith("_KEY") or "API_KEY" in u:
        return True
    return False


def mask_secret(key: str, value: str) -> str:
    """Return a human-readable masked form; empty values show as (empty)."""
    if not value:
        return "(empty)"
    if not is_sensitive_key(key):
        return value
    if len(value) <= 4:
        return "****"
    return "****" + value[-4:]
