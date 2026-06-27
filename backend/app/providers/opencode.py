"""OpenCode Go/Zen adapter.

Three collection strategies, selected by ``OPENCODE_MODE``:

* ``static`` (default, V1 fallback) — exposes configured Go usage limits from
  env vars. No network access required.
* ``playwright`` — drives a persistent headless Chromium session against the
  OpenCode Go dashboard, scrapes Rolling / Weekly / Monthly usage cards, and
  normalizes them into meters. A one-time interactive login (headless=false)
  seeds the persistent profile; subsequent runs are headless.
* ``api`` — uses an OpenCode Go API key (``OPENCODE_GO_AUTH_FILE``) to validate
  auth against ``GET {base_url}/models``, then probes for usage/balance
  endpoints. If a usage endpoint is found, its payload is normalized into
  meters. If no usage endpoint exists, the adapter falls back to the
  Playwright/cookie scraping path (when ``OPENCODE_DASHBOARD_URL`` is set) or
  produces an error meter.

The HTML parser (:mod:`app.providers.opencode_parser`) and the auth-file
reader (:func:`app.providers.opencode_api.read_go_auth_file`) are pure
functions with no HTTP/Playwright dependency, so they are unit-tested in
isolation from the browser fetcher (:mod:`app.providers.opencode_browser`).
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
from .opencode_browser import fetch_dashboard_html
from .opencode_parser import ParsedMeter, parse_opencode_dashboard

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
        if mode == "playwright":
            return await self._fetch_playwright()
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

        # 4. No usage endpoint (or unparseable data) -> fall back to playwright.
        log.info("no OpenCode Go API usage endpoint found; falling back to playwright")
        if self.settings.opencode_dashboard_url:
            return await self._fetch_playwright()
        return [self._error_meter("no usage endpoint found and OPENCODE_DASHBOARD_URL not set for fallback")]

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

    # --- Playwright mode ---------------------------------------------------

    async def _fetch_playwright(self) -> list[UsageMeter]:
        url = self.settings.opencode_dashboard_url
        profile_dir = self.settings.opencode_playwright_profile_dir
        cookies_file = self.settings.opencode_cookies_file
        try:
            html = await fetch_dashboard_html(
                url=url,
                profile_dir=profile_dir,
                headless=self.settings.opencode_headless,
                cookies_file=cookies_file,
            )
        except ProviderError as exc:
            return [self._error_meter(str(exc))]
        parsed = parse_opencode_dashboard(html)
        if not parsed:
            if _looks_like_login_page(html):
                return [self._error_meter("dashboard is not authenticated; refresh OPENCODE_COOKIES_FILE or persistent browser profile")]
            return [self._error_meter("no usage cards found on dashboard")]
        return self._normalize_parsed(parsed)

    def _normalize_parsed(self, parsed: list[ParsedMeter]) -> list[UsageMeter]:
        now = utcnow()
        meters: list[UsageMeter] = []
        for item in parsed:
            meter_id = f"opencode-{item.key}"
            used_pct = item.used_percent
            remaining_pct = compute_remaining(used_pct, None)
            meters.append(
                UsageMeter(
                    id=meter_id,
                    provider=self.provider_id,
                    account_id=_ACCOUNT_ID,
                    account_label=self.settings.opencode_label,
                    label=item.label,
                    used_percent=used_pct,
                    remaining_percent=remaining_pct,
                    reset_at=None,
                    reset_label=item.reset_label,
                    status=derive_status(remaining_pct),
                    updated_at=now,
                    metrics=merge_metrics(
                        used=float(used_pct) if used_pct is not None else None,
                        limit=100.0,
                        unit="percent",
                    ),
                )
            )
        return meters

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


def _looks_like_login_page(html: str) -> bool:
    text = html.lower()
    return "openauth" in text or (
        "continue with github" in text and "continue with google" in text
    )


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
