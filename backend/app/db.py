"""SQLite persistence layer.

Uses the stdlib ``sqlite3`` module to keep V1 dependencies minimal. A connection
is opened per operation (SQLite is local and cheap) with WAL mode for read
concurrency. The schema is idempotent and created on startup.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_accounts (
    id          TEXT PRIMARY KEY,
    provider    TEXT NOT NULL,
    label       TEXT NOT NULL,
    auth_type   TEXT NOT NULL,
    secret_ref  TEXT,
    enabled     INTEGER NOT NULL DEFAULT 1,
    config      TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_meters (
    id              TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL,
    provider        TEXT NOT NULL,
    label           TEXT NOT NULL,
    used_percent    INTEGER,
    remaining_percent INTEGER,
    reset_at        TEXT,
    reset_label     TEXT,
    status          TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    metrics         TEXT
);

CREATE TABLE IF NOT EXISTS usage_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    meter_id        TEXT NOT NULL,
    account_id      TEXT NOT NULL,
    provider        TEXT NOT NULL,
    label           TEXT NOT NULL,
    used_percent    INTEGER,
    remaining_percent INTEGER,
    reset_at        TEXT,
    status          TEXT NOT NULL,
    captured_at     TEXT NOT NULL,
    metrics         TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_meter ON usage_snapshots(meter_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_captured ON usage_snapshots(captured_at);

CREATE TABLE IF NOT EXISTS sync_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider        TEXT,
    account_id      TEXT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL,
    error           TEXT,
    meters_written  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_syncruns_provider ON sync_runs(provider);
"""


def init_db(db_path: Path) -> None:
    """Create the database file (parent dirs) and apply the schema."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
    finally:
        conn.close()


def connection(db_path: Path) -> contextmanager:
    """Return a context manager yielding a configured connection."""
    return _connect(db_path)
