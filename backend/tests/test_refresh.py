"""Refresh orchestration tests using the static OpenCode provider (no network)."""

from __future__ import annotations

import pytest

from app.models import UsageMeter
from app.refresh import refresh_all
from app.repository import load_meters, upsert_account, write_meters
from tests.conftest import make_settings


@pytest.mark.asyncio
async def test_refresh_all_persists_static_provider(db_path):
    settings = make_settings(
        db_path,
        opencode_enabled=True,
        opencode_monthly_limit_usd=20.0,
        opencode_monthly_used_usd=4.0,
    )
    summary = await refresh_all(settings)
    assert len(summary.meters) == 1
    assert summary.meters[0].provider == "opencode"
    assert summary.meters[0].remaining_percent == 80

    persisted = load_meters(db_path)
    assert len(persisted) == 1
    assert persisted[0].used_percent == 20


@pytest.mark.asyncio
async def test_refresh_all_with_no_providers(db_path):
    settings = make_settings(db_path)
    summary = await refresh_all(settings)
    assert summary.meters == []
    assert summary.alerts == []


@pytest.mark.asyncio
async def test_refresh_all_removes_disabled_provider_latest_meters(db_path):
    upsert_account(
        db_path,
        account_id="opencode-go",
        provider="opencode",
        label="OpenCode Go",
        auth_type="static",
        secret_ref=None,
    )
    write_meters(
        db_path,
        [
            UsageMeter(
                id="opencode-go-error",
                provider="opencode",
                account_id="opencode-go",
                label="OpenCode Go",
                status="error",
                reset_label="old error",
            )
        ],
    )

    summary = await refresh_all(make_settings(db_path, opencode_enabled=False))
    assert summary.meters == []
    assert load_meters(db_path) == []
