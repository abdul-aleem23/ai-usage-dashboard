"""Tests for normalization helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import UsageMeter, utcnow
from app.normalizer import (
    build_alerts,
    compute_remaining,
    derive_status,
    format_reset_label,
)


def test_derive_status_thresholds():
    assert derive_status(50) == "ok"
    assert derive_status(25) == "ok"
    assert derive_status(24) == "warning"
    assert derive_status(10) == "warning"
    assert derive_status(9) == "critical"
    assert derive_status(None) == "unknown"


def test_compute_remaining_fills_complement():
    assert compute_remaining(2, None) == 98
    assert compute_remaining(None, 30) == 30
    assert compute_remaining(110, None) == 0  # clamped
    assert compute_remaining(None, None) is None


def test_format_reset_label_future():
    now = datetime(2026, 6, 27, 18, 46, tzinfo=timezone.utc)
    reset = now + timedelta(hours=4, minutes=12)
    assert format_reset_label(reset, now) == "Resets in 4h 12m"


def test_format_reset_label_minutes_only():
    now = datetime(2026, 6, 27, 18, 46, tzinfo=timezone.utc)
    reset = now + timedelta(minutes=35)
    assert format_reset_label(reset, now) == "Resets in 35m"


def test_format_reset_label_past():
    now = datetime(2026, 6, 27, 18, 46, tzinfo=timezone.utc)
    reset = now - timedelta(minutes=5)
    assert format_reset_label(reset, now) == "Resets now"


def test_format_reset_label_none():
    assert format_reset_label(None) is None


def test_build_alerts_filters_by_status():
    meters = [
        UsageMeter(id="a", provider="codex", label="5h", remaining_percent=5, status="critical"),
        UsageMeter(id="b", provider="codex", label="wk", remaining_percent=20, status="warning"),
        UsageMeter(id="c", provider="codex", label="ok", remaining_percent=80, status="ok"),
        UsageMeter(id="d", provider="codex", label="err", status="error", reset_label="boom"),
    ]
    alerts = build_alerts(meters)
    levels = sorted(a.level for a in alerts)
    assert levels == ["critical", "critical", "warning"]
    assert all(a.provider == "codex" for a in alerts)
