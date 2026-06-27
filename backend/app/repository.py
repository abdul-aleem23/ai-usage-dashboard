"""Persistence repository for meters, accounts and sync runs."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .db import connection
from .models import ProviderStatus, UsageMeter, utcnow


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone().isoformat() if dt else None


def upsert_account(
    db_path: Path,
    *,
    account_id: str,
    provider: str,
    label: str,
    auth_type: str,
    secret_ref: str | None,
    enabled: bool = True,
    config: dict | None = None,
) -> None:
    now = _iso(utcnow())
    with connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO provider_accounts (id, provider, label, auth_type, secret_ref, enabled, config, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                provider=excluded.provider,
                label=excluded.label,
                auth_type=excluded.auth_type,
                secret_ref=excluded.secret_ref,
                enabled=excluded.enabled,
                config=excluded.config,
                updated_at=excluded.updated_at
            """,
            (
                account_id,
                provider,
                label,
                auth_type,
                secret_ref,
                1 if enabled else 0,
                json.dumps(config) if config else None,
                now,
                now,
            ),
        )
        conn.commit()



def disable_provider_accounts(db_path: Path, provider: str) -> None:
    """Mark all accounts for a disabled provider as disabled."""
    now = _iso(utcnow())
    with connection(db_path) as conn:
        conn.execute(
            "UPDATE provider_accounts SET enabled = 0, updated_at = ? WHERE provider = ?",
            (now, provider),
        )
        conn.commit()


def delete_latest_meters_for_provider(db_path: Path, provider: str) -> int:
    """Remove latest dashboard rows for a disabled provider, keeping snapshots."""
    with connection(db_path) as conn:
        cur = conn.execute("DELETE FROM usage_meters WHERE provider = ?", (provider,))
        conn.commit()
        return cur.rowcount

def write_meters(db_path: Path, meters: Iterable[UsageMeter]) -> int:
    """Replace latest meters for the given account_ids and snapshot them."""
    meter_list = list(meters)
    if not meter_list:
        return 0

    now = _iso(utcnow())
    account_ids = {m.account_id or "" for m in meter_list}
    meter_ids = {m.id for m in meter_list}
    with connection(db_path) as conn:
        for account_id in account_ids:
            conn.execute(
                f"DELETE FROM usage_meters WHERE account_id = ? AND id NOT IN ({','.join('?' for _ in meter_ids)})",
                (account_id, *meter_ids),
            )
        for m in meter_list:
            metrics_json = m.metrics.model_dump_json() if m.metrics else None
            conn.execute(
                """
                INSERT INTO usage_meters
                    (id, account_id, provider, label, used_percent, remaining_percent,
                     reset_at, reset_label, status, updated_at, metrics)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    account_id=excluded.account_id,
                    provider=excluded.provider,
                    label=excluded.label,
                    used_percent=excluded.used_percent,
                    remaining_percent=excluded.remaining_percent,
                    reset_at=excluded.reset_at,
                    reset_label=excluded.reset_label,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    metrics=excluded.metrics
                """,
                (
                    m.id,
                    m.account_id or "",
                    m.provider,
                    m.label,
                    m.used_percent,
                    m.remaining_percent,
                    _iso(m.reset_at),
                    m.reset_label,
                    m.status,
                    _iso(m.updated_at),
                    metrics_json,
                ),
            )
            conn.execute(
                """
                INSERT INTO usage_snapshots
                    (meter_id, account_id, provider, label, used_percent, remaining_percent,
                     reset_at, status, captured_at, metrics)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    m.id,
                    m.account_id or "",
                    m.provider,
                    m.label,
                    m.used_percent,
                    m.remaining_percent,
                    _iso(m.reset_at),
                    m.status,
                    now,
                    metrics_json,
                ),
            )
        conn.commit()
    return len(meter_list)


def load_meters(db_path: Path) -> list[UsageMeter]:
    with connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT usage_meters.*, provider_accounts.label AS account_label
            FROM usage_meters
            LEFT JOIN provider_accounts ON provider_accounts.id = usage_meters.account_id
            ORDER BY usage_meters.provider, usage_meters.id
            """
        ).fetchall()
    return [_row_to_meter(r) for r in rows]


def load_meters_for_provider(db_path: Path, provider_or_account_id: str) -> list[UsageMeter]:
    with connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT usage_meters.*, provider_accounts.label AS account_label
            FROM usage_meters
            LEFT JOIN provider_accounts ON provider_accounts.id = usage_meters.account_id
            WHERE usage_meters.provider = ? OR usage_meters.account_id = ?
            ORDER BY usage_meters.id
            """,
            (provider_or_account_id, provider_or_account_id),
        ).fetchall()
    return [_row_to_meter(r) for r in rows]


def load_provider_statuses(db_path: Path) -> list[ProviderStatus]:
    with connection(db_path) as conn:
        accounts = conn.execute(
            "SELECT * FROM provider_accounts ORDER BY provider, label"
        ).fetchall()
        meter_rows = conn.execute(
            """
            SELECT usage_meters.*, provider_accounts.label AS account_label
            FROM usage_meters
            LEFT JOIN provider_accounts ON provider_accounts.id = usage_meters.account_id
            ORDER BY usage_meters.id
            """
        ).fetchall()
        last_syncs = conn.execute(
            """
            SELECT account_id, MAX(finished_at) AS last_sync, status, error
            FROM sync_runs WHERE account_id IS NOT NULL GROUP BY account_id
            """
        ).fetchall()
    sync_map = {r["account_id"]: r for r in last_syncs}
    meters_by_account: dict[str, list[UsageMeter]] = {}
    for r in meter_rows:
        meters_by_account.setdefault(r["account_id"], []).append(_row_to_meter(r))

    statuses: list[ProviderStatus] = []
    for a in accounts:
        ac_id = a["id"]
        meters = meters_by_account.get(ac_id, [])
        sync = sync_map.get(ac_id)
        agg_status = _aggregate_status(meters)
        statuses.append(
            ProviderStatus(
                id=ac_id,
                provider=a["provider"],
                label=a["label"],
                enabled=bool(a["enabled"]),
                status=agg_status,
                last_sync=_parse_dt(sync["last_sync"]) if sync and sync["last_sync"] else None,
                last_error=sync["error"] if sync else None,
                meters=meters,
            )
        )
    return statuses


def record_sync_run(
    db_path: Path,
    *,
    provider: str,
    account_id: str | None,
    started_at: datetime,
    finished_at: datetime | None,
    status: str,
    error: str | None,
    meters_written: int,
) -> None:
    with connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sync_runs (provider, account_id, started_at, finished_at, status, error, meters_written)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider,
                account_id,
                _iso(started_at),
                _iso(finished_at),
                status,
                error,
                meters_written,
            ),
        )
        conn.commit()


def _row_to_meter(row) -> UsageMeter:
    metrics = None
    if row["metrics"]:
        from .models import MeterMetrics

        metrics = MeterMetrics.model_validate_json(row["metrics"])
    return UsageMeter(
        id=row["id"],
        provider=row["provider"],
        account_id=row["account_id"] or None,
        account_label=_row_value(row, "account_label"),
        label=row["label"],
        used_percent=row["used_percent"],
        remaining_percent=row["remaining_percent"],
        reset_at=_parse_dt(row["reset_at"]),
        reset_label=row["reset_label"],
        status=row["status"],
        updated_at=_parse_dt(row["updated_at"]) or utcnow(),
        metrics=metrics,
    )


def _row_value(row, key: str):
    return row[key] if key in row.keys() else None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _aggregate_status(meters: list[UsageMeter]) -> str:
    if not meters:
        return "unknown"
    priority = {"error": 4, "critical": 3, "warning": 2, "unknown": 1, "ok": 0}
    worst = max(meters, key=lambda m: priority.get(m.status, 0))
    return worst.status
