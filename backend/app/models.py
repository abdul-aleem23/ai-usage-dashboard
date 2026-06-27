"""Normalized dashboard models.

These are the normalized API models shared by provider adapters, persistence,
and response serialization.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    """Timezone-aware UTC now (kept centralized for testability)."""
    return datetime.now(timezone.utc)


class MeterMetrics(BaseModel):
    """Optional quantitative details carried alongside the compact meter."""

    used: float | None = None
    limit: float | None = None
    unit: str | None = None
    tokens_used: int | None = None
    tokens_limit: int | None = None
    cost_used: float | None = None
    cost_limit: float | None = None
    balance: float | None = None
    currency: str | None = None


class UsageMeter(BaseModel):
    """A single normalized quota/usage meter (one dashboard card)."""

    id: str = Field(..., description="Stable meter id, e.g. 'codex-primary-5h'")
    provider: str
    account_id: str | None = None
    account_label: str | None = None
    label: str = Field(..., description="Human label, e.g. '5 hour usage limit'")
    used_percent: int | None = None
    remaining_percent: int | None = None
    reset_at: datetime | None = None
    reset_label: str | None = None
    status: str = Field("unknown", description="ok | warning | critical | unknown | error")
    updated_at: datetime = Field(default_factory=utcnow)
    metrics: MeterMetrics | None = None


class Alert(BaseModel):
    """A dashboard alert derived from meter status."""

    level: str  # info | warning | critical
    provider: str
    meter_id: str | None = None
    message: str


class ProviderStatus(BaseModel):
    """Status of a single provider/account for the providers endpoint."""

    id: str
    provider: str
    label: str
    enabled: bool
    status: str = "unknown"
    last_sync: datetime | None = None
    last_error: str | None = None
    meters: list[UsageMeter] = Field(default_factory=list)


class DashboardSummary(BaseModel):
    """Top-level ESP32 summary payload."""

    generated_at: datetime = Field(default_factory=utcnow)
    meters: list[UsageMeter] = Field(default_factory=list)
    alerts: list[Alert] = Field(default_factory=list)


# --- Compact (ESP32) shapes ----------------------------------------------


class CompactMeter(BaseModel):
    """Minimal meter representation with short keys to save bytes."""

    id: str
    p: str  # provider
    al: str | None = None  # account label
    l: str  # label
    u: int | None = None  # used percent
    r: int | None = None  # remaining percent
    s: str = "unknown"  # status
    rt: str | None = None  # reset_at (ISO)


class CompactAlert(BaseModel):
    level: str
    p: str
    m: str


class CompactSummary(BaseModel):
    ts: str  # generated_at (ISO)
    m: list[CompactMeter] = Field(default_factory=list)
    a: list[CompactAlert] = Field(default_factory=list)
