"""OpenCode adapter tests (static and API modes)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.providers.opencode import OpenCodeAdapter
from tests.conftest import make_settings


@pytest.mark.asyncio
async def test_opencode_static_meter(db_path: Path):
    settings = make_settings(
        db_path,
        opencode_enabled=True,
        opencode_mode="static",
        opencode_monthly_limit_usd=20.0,
        opencode_monthly_used_usd=5.0,
        opencode_label="OpenCode Go",
    )
    adapter = OpenCodeAdapter(settings)
    meters = await adapter.fetch_meters()
    assert len(meters) == 1
    m = meters[0]
    assert m.provider == "opencode"
    assert m.id == "opencode-go-monthly"
    assert m.used_percent == 25
    assert m.remaining_percent == 75
    assert m.status == "ok"
    assert m.metrics.limit == 20.0
    assert m.metrics.cost_used == 5.0
    assert m.reset_at is not None
    assert m.reset_at.tzinfo is not None


@pytest.mark.asyncio
async def test_opencode_critical_when_near_limit(db_path: Path):
    settings = make_settings(
        db_path,
        opencode_enabled=True,
        opencode_monthly_limit_usd=20.0,
        opencode_monthly_used_usd=19.0,
    )
    adapter = OpenCodeAdapter(settings)
    meters = await adapter.fetch_meters()
    assert meters[0].used_percent == 95
    assert meters[0].status == "critical"


@pytest.mark.asyncio
async def test_opencode_disabled_by_default(settings):
    adapter = OpenCodeAdapter(settings)
    assert not adapter.enabled
    assert await adapter.fetch_meters() == []


def test_opencode_mode_validation_rejects_invalid():
    with pytest.raises(Exception):
        make_settings(db_path=Path("."), opencode_enabled=True, opencode_mode="bogus")


def test_opencode_mode_validation_rejects_browser_mode():
    with pytest.raises(Exception):
        make_settings(db_path=Path("."), opencode_enabled=True, opencode_mode="browser")


def test_opencode_mode_accepts_api():
    settings = make_settings(db_path=Path("."), opencode_enabled=True, opencode_mode="api")
    assert settings.opencode_mode == "api"
