"""OpenCode adapter tests (static + playwright modes)."""

from __future__ import annotations

from pathlib import Path

import pytest
from datetime import timezone

from app.providers import opencode
from app.providers.opencode import OpenCodeAdapter
from app.providers.opencode_parser import ParsedMeter
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
    assert m.used_percent == 25  # 5/20
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


# --- Playwright mode (fetcher mocked; no real browser) --------------------


@pytest.mark.asyncio
async def test_opencode_playwright_normalizes_parsed(db_path: Path, monkeypatch):
    settings = make_settings(
        db_path,
        opencode_enabled=True,
        opencode_mode="playwright",
        opencode_dashboard_url="https://opencode.example.com/dashboard",
        opencode_headless=True,
    )
    adapter = OpenCodeAdapter(settings)

    async def fake_fetch(url, profile_dir, headless=True, timeout_ms=30000, cookies_file=None):
        assert url == "https://opencode.example.com/dashboard"
        assert headless is True
        return "<html>unused-by-this-test</html>"

    monkeypatch.setattr(opencode, "fetch_dashboard_html", fake_fetch)

    def fake_parse(html):
        return [
            ParsedMeter(key="rolling", label="Rolling Usage", used_percent=2, reset_label="Resets in 3h 12m"),
            ParsedMeter(key="weekly", label="Weekly Usage", used_percent=12, reset_label="Resets in 2d 4h"),
            ParsedMeter(key="monthly", label="Monthly Usage", used_percent=47, reset_label="Resets at 2026-07-01"),
        ]

    monkeypatch.setattr(opencode, "parse_opencode_dashboard", fake_parse)

    meters = await adapter.fetch_meters()
    assert len(meters) == 3
    by_id = {m.id: m for m in meters}
    assert set(by_id) == {"opencode-rolling", "opencode-weekly", "opencode-monthly"}

    rolling = by_id["opencode-rolling"]
    assert rolling.provider == "opencode"
    assert rolling.account_id == "opencode-go"
    assert rolling.label == "Rolling Usage"
    assert rolling.used_percent == 2
    assert rolling.remaining_percent == 98  # 100 - 2
    assert rolling.status == "ok"
    assert rolling.reset_label == "Resets in 3h 12m"
    assert rolling.reset_at is None  # dashboard doesn't expose absolute reset time

    monthly = by_id["opencode-monthly"]
    assert monthly.used_percent == 47
    assert monthly.remaining_percent == 53


@pytest.mark.asyncio
async def test_opencode_playwright_error_when_fetch_fails(db_path: Path, monkeypatch):
    from app.providers.base import ProviderError

    settings = make_settings(
        db_path,
        opencode_enabled=True,
        opencode_mode="playwright",
        opencode_dashboard_url="https://opencode.example.com/dashboard",
    )
    adapter = OpenCodeAdapter(settings)

    async def failing_fetch(url, profile_dir, headless=True, timeout_ms=30000, cookies_file=None):
        raise ProviderError("browser launch failed")

    monkeypatch.setattr(opencode, "fetch_dashboard_html", failing_fetch)

    meters = await adapter.fetch_meters()
    assert len(meters) == 1
    assert meters[0].status == "error"
    assert "browser launch failed" in meters[0].reset_label


@pytest.mark.asyncio
async def test_opencode_playwright_error_when_no_cards_found(db_path: Path, monkeypatch):
    settings = make_settings(
        db_path,
        opencode_enabled=True,
        opencode_mode="playwright",
        opencode_dashboard_url="https://opencode.example.com/dashboard",
    )
    adapter = OpenCodeAdapter(settings)

    async def fake_fetch(url, profile_dir, headless=True, timeout_ms=30000, cookies_file=None):
        return "<html><body>login page</body></html>"

    monkeypatch.setattr(opencode, "fetch_dashboard_html", fake_fetch)
    # Use the REAL parser against HTML with no usage labels.
    meters = await adapter.fetch_meters()
    assert len(meters) == 1
    assert meters[0].status == "error"
    assert "no usage cards" in meters[0].reset_label


def test_opencode_mode_validation_rejects_invalid():
    import pytest as _pytest

    with _pytest.raises(Exception):
        make_settings(db_path=Path("."), opencode_enabled=True, opencode_mode="bogus")


def test_opencode_mode_accepts_api():
    """'api' is a valid mode value."""
    settings = make_settings(db_path=Path("."), opencode_enabled=True, opencode_mode="api")
    assert settings.opencode_mode == "api"


@pytest.mark.asyncio
async def test_opencode_playwright_passes_cookies_file(db_path: Path, tmp_path: Path, monkeypatch):
    """The adapter forwards OPENCODE_COOKIES_FILE to the browser fetcher."""
    cookies_path = tmp_path / "cookies.json"
    cookies_path.write_text("[]", encoding="utf-8")

    settings = make_settings(
        db_path,
        opencode_enabled=True,
        opencode_mode="playwright",
        opencode_dashboard_url="https://opencode.example.com/dashboard",
        opencode_cookies_file=cookies_path,
    )
    adapter = OpenCodeAdapter(settings)

    received: dict = {}

    async def spy_fetch(url, profile_dir, headless=True, timeout_ms=30000, cookies_file=None):
        received["cookies_file"] = cookies_file
        return "<html><body>login page</body></html>"

    monkeypatch.setattr(opencode, "fetch_dashboard_html", spy_fetch)
    await adapter.fetch_meters()
    assert received["cookies_file"] == cookies_path


@pytest.mark.asyncio
async def test_opencode_playwright_cookies_load_error_becomes_error_meter(
    db_path: Path, tmp_path: Path, monkeypatch
):
    """A bad cookies file surfaces as an error meter, not a crash."""
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{not json", encoding="utf-8")

    settings = make_settings(
        db_path,
        opencode_enabled=True,
        opencode_mode="playwright",
        opencode_dashboard_url="https://opencode.example.com/dashboard",
        opencode_cookies_file=bad_path,
    )
    adapter = OpenCodeAdapter(settings)

    # Use the REAL fetcher so the cookie load is actually attempted; the lazy
    # Playwright import is never reached because load_cookies_from_file fails first.
    meters = await adapter.fetch_meters()
    assert len(meters) == 1
    assert meters[0].status == "error"
    assert "not valid JSON" in meters[0].reset_label

@pytest.mark.asyncio
async def test_opencode_playwright_reports_login_page(db_path: Path, monkeypatch):
    settings = make_settings(
        db_path,
        opencode_enabled=True,
        opencode_mode="playwright",
        opencode_dashboard_url="https://opencode.example.com/dashboard",
    )
    adapter = OpenCodeAdapter(settings)

    async def fake_fetch(url, profile_dir, headless=True, timeout_ms=30000, cookies_file=None):
        return "<html><head><title>OpenAuth</title></head><body>Continue with GitHub Continue with Google</body></html>"

    monkeypatch.setattr(opencode, "fetch_dashboard_html", fake_fetch)
    meters = await adapter.fetch_meters()
    assert len(meters) == 1
    assert meters[0].status == "error"
    assert "not authenticated" in meters[0].reset_label
