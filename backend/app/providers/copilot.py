"""GitHub Copilot personal quota adapter.

Translated from ``QuotaBackend/Sources/QuotaBackend/Providers/CopilotProvider.swift``
(reference only). Behavior:

* Use a GitHub token (env var or mounted file).
* Call ``https://api.github.com/copilot_internal/user``.
* Parse personal quota snapshots / reset times into meters.

``copilot_internal/user`` is not the public enterprise metrics API; the adapter
is kept isolated and parsing is defensive.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from ..models import UsageMeter, utcnow
from ..normalizer import (
    clamp_percent,
    compute_remaining,
    derive_status,
    format_reset_label,
    merge_metrics,
)
from .base import ProviderAdapter, ProviderError

_ACCOUNT_ID = "copilot-personal"


class CopilotAdapter(ProviderAdapter):
    provider_id = "copilot"

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._client: httpx.AsyncClient | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.copilot_token_value())

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.settings.request_timeout_seconds,
                headers={
                    "User-Agent": self.settings.user_agent,
                    "Accept": "application/json",
                    "Editor-Version": "vscode/1.90.0",
                    "Editor-Plugin-Version": "copilot/1.0.0",
                },
            )
        return self._client

    async def fetch_meters(self) -> list[UsageMeter]:
        token = self.settings.copilot_token_value()
        if not token:
            return []
        try:
            payload = await self._fetch_user(token)
        except ProviderError as exc:
            return [self._error_meter(str(exc))]
        except Exception as exc:  # noqa: BLE001
            return [self._error_meter(f"unexpected error: {exc}")]
        return self._normalize(payload)

    async def _fetch_user(self, token: str) -> dict[str, Any]:
        client = await self._get_client()
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        }
        try:
            resp = await client.get(self.settings.copilot_api_url, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"copilot request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(f"copilot request failed: HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderError(f"copilot response is not JSON: {exc}") from exc

    def _normalize(self, payload: dict[str, Any]) -> list[UsageMeter]:
        snapshots = _extract_snapshots(payload)
        if not snapshots:
            return [self._error_meter("no quota snapshots found in response")]
        now = utcnow()
        meters: list[UsageMeter] = []
        for snap in snapshots:
            quota_type = str(snap.get("quota_type") or snap.get("quota_id") or snap.get("type") or "usage")
            used = _first_int(snap, "used", "used_count")
            total = _first_int(snap, "total", "limit", "quota", "entitlement")
            remaining = _first_int(snap, "remaining", "quota_remaining")
            if used is None and remaining is not None and total is not None:
                used = max(0, total - remaining)
            used_pct = _percent(used, total)
            remaining_pct = clamp_percent(_to_int(snap.get("percent_remaining")))
            if used_pct is None and remaining_pct is not None:
                used_pct = clamp_percent(100 - remaining_pct)
            if used_pct is None and remaining is not None and total is not None:
                used_pct = _percent(total - remaining, total)
            remaining_pct = compute_remaining(used_pct, remaining_pct)
            reset_at = _parse_dt(snap.get("resets_at") or snap.get("reset_at") or snap.get("quota_reset_at"))
            label = _LABELS.get(quota_type, f"Copilot {quota_type}")
            meters.append(
                UsageMeter(
                    id=f"{_ACCOUNT_ID}-{quota_type}",
                    provider=self.provider_id,
                    account_id=_ACCOUNT_ID,
                    account_label="Copilot",
                    label=label,
                    used_percent=used_pct,
                    remaining_percent=remaining_pct,
                    reset_at=reset_at,
                    reset_label=format_reset_label(reset_at, now),
                    status=derive_status(remaining_pct),
                    updated_at=now,
                    metrics=merge_metrics(
                        used=float(used) if used is not None else None,
                        limit=float(total) if total is not None else None,
                        unit=snap.get("unit") or "requests",
                        tokens_used=used,
                        tokens_limit=total,
                    ),
                )
            )
        return meters

    def _error_meter(self, message: str) -> UsageMeter:
        return UsageMeter(
            id=f"{_ACCOUNT_ID}-error",
            provider=self.provider_id,
            account_id=_ACCOUNT_ID,
            account_label="Copilot",
            label="Copilot",
            status="error",
            updated_at=utcnow(),
            reset_label=message,
        )

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


_LABELS = {
    "code_completions": "Copilot code completions",
    "chat": "Copilot chat",
    "standard": "Copilot usage",
}


def _extract_snapshots(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    snapshots = data.get("quota_snapshots") or data.get("quotaSnapshots")
    if isinstance(snapshots, list):
        return [s for s in snapshots if isinstance(s, dict)]
    if isinstance(snapshots, dict):
        reset_at = data.get("quota_reset_date_utc") or data.get("quota_reset_date")
        result: list[dict[str, Any]] = []
        for key, value in snapshots.items():
            if not isinstance(value, dict):
                continue
            if value.get("has_quota") is False and _to_int(value.get("entitlement")) in (None, 0):
                continue
            item = dict(value)
            item.setdefault("quota_type", item.get("quota_id") or key)
            item.setdefault("resets_at", reset_at)
            result.append(item)
        return result
    summary = data.get("quota_summary") or data.get("quotaSummary")
    if isinstance(summary, dict):
        return [summary]
    # Some responses inline a single quota object.
    if "used" in data or "total" in data:
        return [data]
    return []


def _percent(used: int | None, total: int | None) -> int | None:
    if used is None or total is None or total == 0:
        return None
    return clamp_percent(round((used / total) * 100))


def _first_int(data: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key in data:
            value = _to_int(data.get(key))
            if value is not None:
                return value
    return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
