"""Summary endpoints (ESP32-facing).

``GET /api/v1/summary``      -> full normalized payload.
``GET /api/v1/summary.compact`` -> minimal short-key payload for tight memory.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require_read_key
from ..config import Settings, get_settings
from ..models import (
    CompactAlert,
    CompactMeter,
    CompactSummary,
    DashboardSummary,
    UsageMeter,
    utcnow,
)
from ..normalizer import build_alerts
from ..repository import load_meters

router = APIRouter(prefix="/api/v1", tags=["summary"])


def _build_summary(settings: Settings) -> DashboardSummary:
    meters = load_meters(settings.db_path)
    return DashboardSummary(
        generated_at=utcnow(),
        meters=meters,
        alerts=build_alerts(meters),
    )


@router.get("/summary", response_model=DashboardSummary)
async def get_summary(
    _key: str = Depends(require_read_key),
    settings: Settings = Depends(get_settings),
) -> DashboardSummary:
    return _build_summary(settings)


@router.get("/summary.compact", response_model=CompactSummary)
async def get_summary_compact(
    _key: str = Depends(require_read_key),
    settings: Settings = Depends(get_settings),
) -> CompactSummary:
    summary = _build_summary(settings)
    return _to_compact(summary)


def _to_compact(summary: DashboardSummary) -> CompactSummary:
    meters = [
        CompactMeter(
            id=m.id,
            p=m.provider,
            al=m.account_label,
            l=m.label,
            u=m.used_percent,
            r=m.remaining_percent,
            s=m.status,
            rt=m.reset_at.isoformat() if m.reset_at else None,
        )
        for m in summary.meters
    ]
    alerts = [CompactAlert(level=a.level, p=a.provider, m=a.message) for a in summary.alerts]
    return CompactSummary(
        ts=summary.generated_at.isoformat(),
        m=meters,
        a=alerts,
    )
