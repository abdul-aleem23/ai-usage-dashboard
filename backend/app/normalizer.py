"""Normalization helpers shared by provider adapters and the refresh layer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import Alert, MeterMetrics, utcnow

# Status thresholds on remaining_percent.
WARNING_BELOW = 25
CRITICAL_BELOW = 10


def derive_status(remaining_percent: int | None) -> str:
    """Map a remaining percentage onto a status string."""
    if remaining_percent is None:
        return "unknown"
    if remaining_percent < CRITICAL_BELOW:
        return "critical"
    if remaining_percent < WARNING_BELOW:
        return "warning"
    return "ok"


def compute_remaining(used_percent: int | None, remaining_percent: int | None) -> int | None:
    """Fill in the complementary percentage if only one was provided."""
    if remaining_percent is not None:
        return max(0, min(100, remaining_percent))
    if used_percent is not None:
        return max(0, min(100, 100 - used_percent))
    return None


def format_reset_label(reset_at: datetime | None, now: datetime | None = None) -> str | None:
    """Render a human 'Resets in Xh Ym' label relative to ``now``."""
    if reset_at is None:
        return None
    if reset_at.tzinfo is None:
        reset_at = reset_at.replace(tzinfo=timezone.utc)
    now = now or utcnow()
    delta = reset_at - now
    if delta.total_seconds() <= 0:
        return "Resets now"
    return "Resets in " + _humanize_delta(delta)


def _humanize_delta(delta: timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes and not days:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append("0m")
    return " ".join(parts)


def build_alerts(meters) -> list[Alert]:
    """Derive dashboard alerts from meter statuses."""
    alerts: list[Alert] = []
    for m in meters:
        if m.status == "critical":
            alerts.append(
                Alert(
                    level="critical",
                    provider=m.provider,
                    meter_id=m.id,
                    message=f"{m.label}: {m.remaining_percent}% remaining",
                )
            )
        elif m.status == "warning":
            alerts.append(
                Alert(
                    level="warning",
                    provider=m.provider,
                    meter_id=m.id,
                    message=f"{m.label}: {m.remaining_percent}% remaining",
                )
            )
        elif m.status == "error":
            alerts.append(
                Alert(
                    level="critical",
                    provider=m.provider,
                    meter_id=m.id,
                    message=f"{m.label}: refresh failed",
                )
            )
    return alerts


def clamp_percent(value: int | None) -> int | None:
    if value is None:
        return None
    return max(0, min(100, value))


def merge_metrics(**fields) -> MeterMetrics:
    """Build a MeterMetrics object, dropping None values."""
    return MeterMetrics(**{k: v for k, v in fields.items() if v is not None})
