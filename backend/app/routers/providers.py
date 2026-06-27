"""Provider listing / detail endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import require_read_key
from ..config import Settings, get_settings
from ..repository import load_meters_for_provider, load_provider_statuses

router = APIRouter(prefix="/api/v1/providers", tags=["providers"])


@router.get("")
async def list_providers(
    _key: str = Depends(require_read_key),
    settings: Settings = Depends(get_settings),
):
    return load_provider_statuses(settings.db_path)


@router.get("/{provider_id}")
async def get_provider(
    provider_id: str,
    _key: str = Depends(require_read_key),
    settings: Settings = Depends(get_settings),
):
    statuses = load_provider_statuses(settings.db_path)
    match = [s for s in statuses if s.id == provider_id or s.provider == provider_id]
    if not match:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="provider not found")
    if len(match) == 1:
        return match[0]
    return match
