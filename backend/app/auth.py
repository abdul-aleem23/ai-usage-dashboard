"""API-key authentication.

Read-only endpoints (summary, providers) accept either the ESP32 read-only key
or the admin key. The admin refresh endpoint requires the admin key.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from .config import Settings, get_settings


def _extract_key(x_api_key: str | None, authorization: str | None) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def require_read_key(
    settings: Settings = Depends(get_settings),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> str:
    """Dependency for read-only endpoints: ESP32 or admin key."""
    key = _extract_key(x_api_key, authorization)
    if key and (key == settings.esp32_api_key or key == settings.admin_api_key):
        return key
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid API key",
        headers={"WWW-Authenticate": 'ApiKey realm="ai-usage"'},
    )


def require_admin_key(
    settings: Settings = Depends(get_settings),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> str:
    """Dependency for admin endpoints: admin key only."""
    key = _extract_key(x_api_key, authorization)
    if key and key == settings.admin_api_key:
        return key
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Admin API key required",
        headers={"WWW-Authenticate": 'ApiKey realm="ai-usage-admin"'},
    )
