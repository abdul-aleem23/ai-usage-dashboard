"""DeepSeek balance adapter.

Calls the official ``GET https://api.deepseek.com/user/balance`` endpoint and
exposes the current balance as a meter. Token usage tracking is deferred until
requests are routed through this backend (V1.1+).
"""

from __future__ import annotations

from typing import Any

import httpx

from ..models import UsageMeter, utcnow
from ..normalizer import clamp_percent, format_reset_label, merge_metrics
from .base import ProviderAdapter, ProviderError

_ACCOUNT_ID = "deepseek-default"


class DeepSeekAdapter(ProviderAdapter):
    provider_id = "deepseek"

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._client: httpx.AsyncClient | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.deepseek_api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.settings.request_timeout_seconds,
                headers={
                    "User-Agent": self.settings.user_agent,
                    "Accept": "application/json",
                },
            )
        return self._client

    async def fetch_meters(self) -> list[UsageMeter]:
        if not self.enabled:
            return []
        try:
            payload = await self._fetch_balance()
        except ProviderError as exc:
            return [self._error_meter(str(exc))]
        except Exception as exc:  # noqa: BLE001
            return [self._error_meter(f"unexpected error: {exc}")]
        return self._normalize(payload)

    async def _fetch_balance(self) -> dict[str, Any]:
        client = await self._get_client()
        url = f"{self.settings.deepseek_base_url.rstrip('/')}/user/balance"
        headers = {"Authorization": f"Bearer {self.settings.deepseek_api_key}"}
        try:
            resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"deepseek request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(f"deepseek request failed: HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderError(f"deepseek response is not JSON: {exc}") from exc

    def _normalize(self, payload: dict[str, Any]) -> list[UsageMeter]:
        infos = payload.get("balance_infos") or []
        if not infos:
            return [self._error_meter("no balance_infos in response")]
        now = utcnow()
        meters: list[UsageMeter] = []
        for info in infos:
            if not isinstance(info, dict):
                continue
            currency = info.get("currency") or "CNY"
            balance = _to_float(info.get("total_balance"))
            topped = _to_float(info.get("topped_up_balance"))
            target = self.settings.deepseek_balance_target_usd if currency == "USD" else None
            remaining_pct = _balance_percent(balance, target)
            used_pct = clamp_percent(100 - remaining_pct) if remaining_pct is not None else None
            metrics = merge_metrics(
                balance=balance,
                currency=currency,
                cost_used=topped if topped is not None else None,
                cost_limit=target,
            )
            status = _balance_status(balance, currency, self.settings.deepseek_low_balance_usd)
            reset_label = _balance_label(balance, target, currency)
            meters.append(
                UsageMeter(
                    id=f"{_ACCOUNT_ID}-{currency}",
                    provider=self.provider_id,
                    account_id=_ACCOUNT_ID,
                    account_label="DeepSeek",
                    label=f"DeepSeek wallet ({currency})",
                    used_percent=used_pct,
                    remaining_percent=remaining_pct,
                    reset_at=None,
                    reset_label=reset_label,
                    status=status,
                    updated_at=now,
                    metrics=metrics,
                )
            )
        return meters or [self._error_meter("no usable balance entries")]

    def _error_meter(self, message: str) -> UsageMeter:
        return UsageMeter(
            id=f"{_ACCOUNT_ID}-error",
            provider=self.provider_id,
            account_id=_ACCOUNT_ID,
            account_label="DeepSeek",
            label="DeepSeek balance",
            status="error",
            updated_at=utcnow(),
            reset_label=message,
        )

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


def _balance_percent(balance: float | None, target: float | None) -> int | None:
    if balance is None or target is None or target <= 0:
        return None
    return clamp_percent(round((balance / target) * 100))


def _balance_status(balance: float | None, currency: str, low_balance_usd: float) -> str:
    if balance is None:
        return "unknown"
    if balance <= 0:
        return "critical"
    if currency == "USD" and balance < low_balance_usd:
        return "warning"
    return "ok"


def _balance_label(balance: float | None, target: float | None, currency: str) -> str | None:
    if balance is None:
        return None
    if target is not None and target > 0:
        return f"{balance:.2f} / {target:.2f} {currency}"
    return f"{balance:.2f} {currency}"


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
