"""OpenCode Go/Zen adapter.

Two collection strategies are supported, selected by ``OPENCODE_MODE``:

* ``static`` (default) exposes configured Go usage limits from env vars.
* ``api`` uses an OpenCode Go API key to validate auth and probe usage/balance
  endpoints. If no usage endpoint exists, the adapter reports a clear error.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from ..models import UsageMeter, utcnow
from ..normalizer import compute_remaining, derive_status, format_reset_label, merge_metrics
from .base import ProviderAdapter, ProviderError
from .opencode_api import (
    normalize_usage,
    probe_usage_endpoints,
    read_go_auth_file,
    validate_auth,
)

log = logging.getLogger(__name__)

_ACCOUNT_ID = "opencode-go"


class OpenCodeAdapter(ProviderAdapter):
    provider_id = "opencode"

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._client: httpx.AsyncClient | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.opencode_enabled

    async def fetch_meters(self) -> list[UsageMeter]:
        if not self.enabled:
            return []
        mode = self.settings.opencode_mode
        if mode == "api":
            return await self._fetch_api()
        return self._fetch_static()

    # --- API mode ----------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.settings.request_timeout_seconds,
                headers={"User-Agent": self.settings.user_agent},
            )
        return self._client

    async def _fetch_api(self) -> list[UsageMeter]:
        # 1. Read + validate the auth file.
        try:
            api_key = read_go_auth_file(self.settings.opencode_go_auth_file)
        except ProviderError as exc:
            return [self._error_meter(str(exc))]

        client = await self._get_client()
        base_url = self.settings.opencode_api_base_url

        # 2. Validate auth against /models.
        valid = await validate_auth(client, base_url, api_key)
        if not valid:
            return [self._error_meter("API key validation failed (check OPENCODE_GO_AUTH_FILE)")]

        # 3. Probe for usage/balance endpoints.
        payload = await probe_usage_endpoints(client, base_url, api_key)
        if payload is not None:
            meters = normalize_usage(payload, self.settings.opencode_label)
            if meters:
                return meters
            log.info("OpenCode Go usage endpoint returned data but no meters could be normalized")

        return [self._error_meter("no OpenCode Go usage endpoint returned parseable usage data")]

    # --- Static mode (fallback) -------------------------------------------

    def _fetch_static(self) -> list[UsageMeter]:
        now = utcnow()
        limit = self.settings.opencode_monthly_limit_usd
        used = self.settings.opencode_monthly_used_usd
        used_pct = _pct(used, limit)
        remaining_pct = compute_remaining(used_pct, None)
        reset_at = _next_reset(now, self.settings.opencode_reset_day_of_month)
        return [
            UsageMeter(
                id=f"{_ACCOUNT_ID}-monthly",
                provider=self.provider_id,
                account_id=_ACCOUNT_ID,
                account_label=self.settings.opencode_label,
                label=f"{self.settings.opencode_label} monthly",
                used_percent=used_pct,
                remaining_percent=remaining_pct,
                reset_at=reset_at,
                reset_label=format_reset_label(reset_at, now),
                status=derive_status(remaining_pct),
                updated_at=now,
                metrics=merge_metrics(
                    used=float(used),
                    limit=float(limit),
                    unit="USD",
                    cost_used=float(used),
                    cost_limit=float(limit),
                ),
            )
        ]

    # --- Shared helpers ----------------------------------------------------

    def _error_meter(self, message: str) -> UsageMeter:
        return UsageMeter(
            id=f"{_ACCOUNT_ID}-error",
            provider=self.provider_id,
            account_id=_ACCOUNT_ID,
            account_label=self.settings.opencode_label,
            label=self.settings.opencode_label,
            status="error",
            updated_at=utcnow(),
            reset_label=message,
        )

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# --- static-mode helpers --------------------------------------------------


def _pct(used: float, limit: float) -> int | None:
    if limit <= 0:
        return None
    return max(0, min(100, round((used / limit) * 100)))


def _next_reset(now: datetime, day_of_month: int) -> datetime:
    day = max(1, min(28, int(day_of_month)))
    candidate = now.replace(year=now.year, month=now.month, day=day, hour=0, minute=0, second=0, microsecond=0)
    if candidate <= now:
        # Next month
        if now.month == 12:
            candidate = candidate.replace(year=now.year + 1, month=1)
        else:
            candidate = candidate.replace(month=now.month + 1)
    return candidate.astimezone(timezone.utc) if candidate.tzinfo else candidate.replace(tzinfo=timezone.utc)
