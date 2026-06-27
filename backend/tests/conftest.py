"""Test configuration.

Sets safe env defaults before any app imports so the module-level app in
``app.main`` can construct, and provides shared fixtures (temp DB, settings,
test client).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Set required env BEFORE importing app modules.
_TMP = Path(tempfile.gettempdir()) / "aiusage-test"
_TMP.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("ESP32_API_KEY", "test-esp32-key")
os.environ.setdefault("DB_PATH", str(_TMP / "aiusage-test.db"))
os.environ.setdefault("REFRESH_ON_STARTUP", "false")
os.environ.setdefault("REFRESH_INTERVAL_MINUTES", "999")

import pytest  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import init_db  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    import json

    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    init_db(path)
    return path


@pytest.fixture()
def settings(db_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        admin_api_key="test-admin-key",
        esp32_api_key="test-esp32-key",
        db_path=db_path,
        refresh_on_startup=False,
        refresh_interval_minutes=999,
        request_timeout_seconds=5.0,
    )


def make_settings(db_path: Path, **overrides) -> Settings:
    """Build a Settings instance with explicit overrides (no .env, no env reads
    for the overridden fields)."""
    defaults = dict(
        _env_file=None,
        admin_api_key="test-admin-key",
        esp32_api_key="test-esp32-key",
        db_path=db_path,
        refresh_on_startup=False,
        refresh_interval_minutes=999,
        request_timeout_seconds=5.0,
    )
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[call-arg]
