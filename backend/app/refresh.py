"""Refresh orchestration.

Runs all enabled provider adapters, persists their meters + accounts, records
sync runs, and returns the resulting :class:`DashboardSummary`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from .models import DashboardSummary, UsageMeter, utcnow
from .normalizer import build_alerts
from .providers import get_adapters
from .repository import (
    delete_latest_meters_for_provider,
    disable_provider_accounts,
    load_meters,
    record_sync_run,
    upsert_account,
    write_meters,
)

if TYPE_CHECKING:
    from .config import Settings

log = logging.getLogger(__name__)


async def refresh_all(settings: "Settings") -> DashboardSummary:
    """Fetch every enabled provider and persist the results."""
    adapters = get_adapters(settings)
    started = utcnow()
    all_meters: list[UsageMeter] = []

    # Register accounts up-front so providers with no live data still appear.
    _register_accounts(settings)

    for adapter in adapters:
        if not adapter.enabled:
            disable_provider_accounts(settings.db_path, adapter.provider_id)
            delete_latest_meters_for_provider(settings.db_path, adapter.provider_id)
            continue
        provider_started = utcnow()
        try:
            meters = await adapter.fetch_meters()
            all_meters.extend(meters)
            record_sync_run(
                settings.db_path,
                provider=adapter.provider_id,
                account_id=None,
                started_at=provider_started,
                finished_at=utcnow(),
                status="ok" if not any(m.status == "error" for m in meters) else "error",
                error=None,
                meters_written=len(meters),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("provider %s failed", adapter.provider_id)
            record_sync_run(
                settings.db_path,
                provider=adapter.provider_id,
                account_id=None,
                started_at=provider_started,
                finished_at=utcnow(),
                status="error",
                error=str(exc),
                meters_written=0,
            )
        finally:
            await adapter.aclose()

    write_meters(settings.db_path, all_meters)
    persisted = load_meters(settings.db_path)
    summary = DashboardSummary(
        generated_at=started,
        meters=persisted,
        alerts=build_alerts(persisted),
    )
    log.info("refresh complete: %d meters in %.2fs", len(persisted), (utcnow() - started).total_seconds())
    return summary


def _register_accounts(settings: "Settings") -> None:
    """Upsert provider_account rows for configured accounts (secrets not stored)."""
    from .config import CodexAccountConfig

    # Codex accounts
    for account in settings.codex_account_configs():
        upsert_account(
            settings.db_path,
            account_id=account.account_id,
            provider="codex",
            label=account.label,
            auth_type="oauth",
            secret_ref=str(account.auth_file),
            enabled=True,
        )

    # Copilot account
    if settings.copilot_token_value():
        upsert_account(
            settings.db_path,
            account_id="copilot-personal",
            provider="copilot",
            label="Copilot",
            auth_type="token",
            secret_ref="copilot_token",
            enabled=True,
        )

    # DeepSeek account
    if settings.deepseek_api_key:
        upsert_account(
            settings.db_path,
            account_id="deepseek-default",
            provider="deepseek",
            label="DeepSeek",
            auth_type="api_key",
            secret_ref="deepseek_api_key",
            enabled=True,
        )

    # OpenCode account
    if settings.opencode_enabled:
        upsert_account(
            settings.db_path,
            account_id="opencode-go",
            provider="opencode",
            label=settings.opencode_label,
            auth_type="static",
            secret_ref=None,
            enabled=True,
            config={
                "monthly_limit_usd": settings.opencode_monthly_limit_usd,
                "monthly_used_usd": settings.opencode_monthly_used_usd,
            },
        )
