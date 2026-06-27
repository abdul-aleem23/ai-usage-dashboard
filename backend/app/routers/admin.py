"""Admin endpoints (admin API key required)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require_admin_key
from ..config import Settings, get_settings
from ..models import DashboardSummary
from ..refresh import refresh_all

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.post("/refresh", response_model=DashboardSummary)
async def trigger_refresh(
    _key: str = Depends(require_admin_key),
    settings: Settings = Depends(get_settings),
) -> DashboardSummary:
    return await refresh_all(settings)
