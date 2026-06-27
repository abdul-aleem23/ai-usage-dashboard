"""Persistence layer tests (meters, snapshots, sync runs)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.models import MeterMetrics, UsageMeter
from app.repository import (
    load_meters,
    load_meters_for_provider,
    load_provider_statuses,
    record_sync_run,
    upsert_account,
    write_meters,
)


def _meter(account_id="codex-personal", status="ok", remaining=80) -> UsageMeter:
    return UsageMeter(
        id=f"{account_id}-5h",
        provider="codex",
        account_id=account_id,
        account_label="Personal",
        label="5 hour usage limit",
        used_percent=100 - remaining,
        remaining_percent=remaining,
        reset_at=datetime(2026, 6, 27, 22, 58, tzinfo=timezone.utc),
        reset_label="Resets in 4h",
        status=status,
        updated_at=datetime(2026, 6, 27, 18, 46, tzinfo=timezone.utc),
        metrics=MeterMetrics(tokens_used=20, tokens_limit=100, unit="tokens"),
    )


def test_write_and_load_meters(db_path):
    upsert_account(
        db_path,
        account_id="codex-personal",
        provider="codex",
        label="Personal",
        auth_type="oauth",
        secret_ref="/secrets/codex.json",
    )
    write_meters(db_path, [_meter(remaining=80)])
    loaded = load_meters(db_path)
    assert len(loaded) == 1
    assert loaded[0].remaining_percent == 80
    assert loaded[0].metrics.tokens_used == 20

    # Snapshot should be recorded.
    with sqlite3.connect(str(db_path)) as conn:
        n = conn.execute("SELECT COUNT(*) FROM usage_snapshots").fetchone()[0]
    assert n == 1


def test_write_meters_upserts(db_path):
    upsert_account(db_path, account_id="codex-personal", provider="codex", label="P", auth_type="oauth", secret_ref="x")
    write_meters(db_path, [_meter(remaining=80)])
    write_meters(db_path, [_meter(remaining=50)])
    loaded = load_meters(db_path)
    assert len(loaded) == 1
    assert loaded[0].remaining_percent == 50
    # Two snapshots now.
    with sqlite3.connect(str(db_path)) as conn:
        n = conn.execute("SELECT COUNT(*) FROM usage_snapshots").fetchone()[0]
    assert n == 2


def test_load_meters_for_provider(db_path):
    upsert_account(db_path, account_id="codex-personal", provider="codex", label="P", auth_type="oauth", secret_ref="x")
    write_meters(db_path, [_meter()])
    assert len(load_meters_for_provider(db_path, "codex")) == 1
    assert len(load_meters_for_provider(db_path, "codex-personal")) == 1
    assert len(load_meters_for_provider(db_path, "deepseek")) == 0


def test_provider_statuses_aggregate(db_path):
    upsert_account(db_path, account_id="codex-personal", provider="codex", label="P", auth_type="oauth", secret_ref="x")
    write_meters(db_path, [_meter(status="critical", remaining=5)])
    record_sync_run(
        db_path,
        provider="codex",
        account_id="codex-personal",
        started_at=datetime(2026, 6, 27, 18, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 6, 27, 18, 1, tzinfo=timezone.utc),
        status="ok",
        error=None,
        meters_written=1,
    )
    statuses = load_provider_statuses(db_path)
    assert len(statuses) == 1
    assert statuses[0].status == "critical"
    assert statuses[0].last_sync is not None


def test_write_meters_replaces_stale_rows_for_account(db_path):
    upsert_account(db_path, account_id="copilot-personal", provider="copilot", label="Copilot", auth_type="token", secret_ref="x")
    write_meters(
        db_path,
        [
            UsageMeter(
                id="copilot-personal-error",
                provider="copilot",
                account_id="copilot-personal",
                label="Copilot",
                status="error",
                reset_label="old error",
            )
        ],
    )
    write_meters(
        db_path,
        [
            UsageMeter(
                id="copilot-personal-chat",
                provider="copilot",
                account_id="copilot-personal",
                label="Copilot chat",
                status="ok",
                remaining_percent=88,
            )
        ],
    )

    loaded = load_meters(db_path)
    assert [m.id for m in loaded] == ["copilot-personal-chat"]
    assert loaded[0].account_label == "Copilot"
