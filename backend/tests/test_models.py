"""Tests for normalized models and serialization."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models import (
    CompactMeter,
    CompactSummary,
    DashboardSummary,
    MeterMetrics,
    UsageMeter,
)


def test_usage_meter_canonical_shape():
    m = UsageMeter(
        id="codex-primary-5h",
        provider="codex",
        label="5 hour usage limit",
        used_percent=2,
        remaining_percent=98,
        reset_at=datetime(2026, 6, 27, 22, 58, tzinfo=timezone.utc),
        reset_label="Resets in 4h 12m",
        status="ok",
        updated_at=datetime(2026, 6, 27, 18, 46, tzinfo=timezone.utc),
        metrics=MeterMetrics(tokens_used=12, tokens_limit=100, unit="tokens"),
    )
    dumped = m.model_dump(mode="json")
    assert dumped["id"] == "codex-primary-5h"
    assert dumped["remaining_percent"] == 98
    assert dumped["status"] == "ok"
    assert dumped["metrics"]["tokens_used"] == 12


def test_dashboard_summary_defaults():
    s = DashboardSummary()
    assert s.meters == []
    assert s.alerts == []
    assert s.generated_at is not None


def test_compact_summary_short_keys():
    s = CompactSummary(
        ts="2026-06-27T18:46:00+00:00",
        m=[CompactMeter(id="x", p="codex", l="5h", u=2, r=98, s="ok", rt="2026-06-27T22:58:00+00:00")],
        a=[],
    )
    dumped = s.model_dump(mode="json")
    assert set(dumped.keys()) == {"ts", "m", "a"}
    assert dumped["m"][0]["p"] == "codex"
    assert "provider" not in dumped["m"][0]
