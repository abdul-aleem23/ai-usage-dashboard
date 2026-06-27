"""Codex / OpenAI subscription quota adapter.

Translated from ``QuotaBackend/Sources/QuotaBackend/Providers/CodexProvider.swift``
(reference only). Behavior:

* Read a per-account Codex auth JSON file (``CODEX_<LABEL>_AUTH_FILE``).
* Refresh the OpenAI OAuth access token when it is missing or expired.
* Call ``https://chatgpt.com/backend-api/wham/usage`` with the bearer token.
* Normalize the 5-hour / weekly / code-review usage windows into meters.

The ``wham/usage`` endpoint is undocumented and may change; parsing is defensive
and tolerates a few known response shapes.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from ..config import CodexAccountConfig
from ..models import UsageMeter, utcnow
from ..normalizer import (
    clamp_percent,
    compute_remaining,
    derive_status,
    format_reset_label,
    merge_metrics,
)
from .base import ProviderAdapter, ProviderError

# Refresh slightly before the nominal expiry to avoid 401s on edge cases.
_REFRESH_SKEW_SECONDS = 60
# Reuse a device id per process; AIUsage keeps one in the auth file, so we honor
# any persisted value and fall back to a stable random id.
_PROCESS_DEVICE_ID = str(uuid.uuid4())

# Static OpenAI OAuth client id used by the Codex CLI, mirrored from
# AIUsage's ``CodexProvider.oauthClientId``. Used as a fallback when the auth
# file does not carry its own client_id.
_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

# Real wham/usage window layout (AIUsage ``parseResponse``):
#   rate_limit.primary_window             -> 5 hour usage limit
#   rate_limit.secondary_window           -> weekly usage limit
#   code_review_rate_limit.primary_window -> code review usage limit
_REAL_WINDOW_MAP: tuple[tuple[str, str, str, str], ...] = (
    ("rate_limit", "primary_window", "5h", "5 hour usage limit"),
    ("rate_limit", "secondary_window", "weekly", "weekly usage limit"),
    ("code_review_rate_limit", "primary_window", "code_review", "code review usage limit"),
)

# Defensive fallback: known window keys we try to recognize from alternate
# wham/usage payload shapes.
_WINDOW_ALIASES: dict[str, list[str]] = {
    "5h": ["5h", "primary_5h", "five_hour", "5_hour", "primary"],
    "weekly": ["weekly", "week", "7d", "weekly_usage"],
    "code_review": ["code_review", "code-review", "codeReview", "review"],
}


class CodexAdapter(ProviderAdapter):
    provider_id = "codex"

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._client: httpx.AsyncClient | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.codex_accounts.strip())

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.settings.request_timeout_seconds,
                headers={"User-Agent": self.settings.user_agent},
            )
        return self._client

    async def fetch_meters(self) -> list[UsageMeter]:
        if not self.enabled:
            return []
        accounts = self.settings.codex_account_configs()
        meters: list[UsageMeter] = []
        for account in accounts:
            try:
                meters.extend(await self._fetch_account(account))
            except ProviderError as exc:
                meters.append(self._error_meter(account, str(exc)))
            except Exception as exc:  # noqa: BLE001
                meters.append(self._error_meter(account, f"unexpected error: {exc}"))
        return meters

    async def _fetch_account(self, account: CodexAccountConfig) -> list[UsageMeter]:
        auth = self._read_auth_file(account.auth_file)
        device_id = auth.get("device_id") or _PROCESS_DEVICE_ID
        # Resolve the ChatGPT account id once from the loaded auth (mirrors
        # AIUsage's load-time resolution) so it can be sent as a header and is
        # stable across an in-flight token refresh.
        account_id = _resolve_account_id(auth)
        token = await self._ensure_access_token(account, auth, device_id)
        payload = await self._fetch_usage(token, device_id, account_id)
        return self._normalize_windows(account, payload)

    def _read_auth_file(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise ProviderError(f"auth file not found: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProviderError(f"auth file is not valid JSON: {exc}") from exc
        return data

    async def _ensure_access_token(
        self, account: CodexAccountConfig, auth: dict[str, Any], device_id: str
    ) -> str:
        tokens = auth.get("tokens") or {}
        access_token = tokens.get("access_token")
        expires_at = _parse_expiry(tokens.get("expires_at")) or _parse_expiry(auth.get("expires_at"))
        if access_token and not _is_expired(expires_at):
            return access_token
        # Token missing or expired -> refresh.
        refresh_token = tokens.get("refresh_token") or auth.get("refresh_token")
        if not refresh_token:
            raise ProviderError("auth file missing refresh_token")
        # Prefer a client_id persisted in the auth file, then fall back to the
        # static Codex CLI client id (matching AIUsage's hardcoded oauthClientId).
        client_id = auth.get("client_id") or auth.get("OPENAI_CLIENT_ID") or _OAUTH_CLIENT_ID
        new_tokens = await self._refresh_oauth(account, client_id, refresh_token)
        _persist_tokens(account.auth_file, auth, new_tokens, device_id)
        return new_tokens["access_token"]

    async def _refresh_oauth(
        self, account: CodexAccountConfig, client_id: str, refresh_token: str
    ) -> dict[str, Any]:
        client = await self._get_client()
        try:
            resp = await client.post(
                self.settings.codex_oauth_token_url,
                json={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                },
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"OAuth refresh request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(f"OAuth refresh failed: HTTP {resp.status_code}")
        body = resp.json()
        if "access_token" not in body:
            raise ProviderError("OAuth refresh response missing access_token")
        return body

    async def _fetch_usage(
        self, access_token: str, device_id: str, account_id: str | None
    ) -> dict[str, Any]:
        client = await self._get_client()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "OAI-Device-Id": device_id,
            "OAI-Client-Version": "codex-cli",
            "Accept": "application/json",
        }
        # Mirror AIUsage: send the ChatGPT account id header when one is
        # available so multi-account workspaces report the correct quota.
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id
        try:
            resp = await client.get(self.settings.codex_usage_url, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"usage request failed: {exc}") from exc
        if resp.status_code == 401:
            raise ProviderError("usage request returned 401 (token rejected)")
        if resp.status_code >= 400:
            raise ProviderError(f"usage request failed: HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderError(f"usage response is not JSON: {exc}") from exc

    def _normalize_windows(
        self, account: CodexAccountConfig, payload: dict[str, Any]
    ) -> list[UsageMeter]:
        windows = _extract_windows(payload)
        if not windows:
            return [self._error_meter(account, "no usage windows found in response")]
        meters: list[UsageMeter] = []
        now = utcnow()
        for key, label, window in windows:
            used_pct = clamp_percent(_to_int(window.get("used_percent")))
            if used_pct is None:
                used_pct = _percent_from_counts(window.get("used"), window.get("limit"))
            remaining_pct = compute_remaining(used_pct, clamp_percent(_to_int(window.get("remaining_percent"))))
            reset_at = _parse_dt_value(window.get("reset_at"))
            meters.append(
                UsageMeter(
                    id=f"{account.account_id}-{key}",
                    provider=self.provider_id,
                    account_id=account.account_id,
                    account_label=account.label,
                    label=label,
                    used_percent=used_pct,
                    remaining_percent=remaining_pct,
                    reset_at=reset_at,
                    reset_label=format_reset_label(reset_at, now),
                    status=derive_status(remaining_pct),
                    updated_at=now,
                    metrics=merge_metrics(
                        used=_to_float(window.get("used")),
                        limit=_to_float(window.get("limit")),
                        unit=window.get("unit"),
                        tokens_used=_to_int(window.get("used")),
                        tokens_limit=_to_int(window.get("limit")),
                    ),
                )
            )
        return meters

    def _error_meter(self, account: CodexAccountConfig, message: str) -> UsageMeter:
        return UsageMeter(
            id=f"{account.account_id}-error",
            provider=self.provider_id,
            account_id=account.account_id,
            account_label=account.label,
            label=f"{account.label} Codex",
            status="error",
            updated_at=utcnow(),
            metrics=None,
            reset_label=message,
        )

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# --- helpers --------------------------------------------------------------


def _extract_windows(payload: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Pull known windows out of a wham/usage payload.

    The primary path mirrors AIUsage's ``parseResponse``: ``rate_limit`` +
    ``code_review_rate_limit`` blocks with ``primary_window`` / ``secondary_window``
    children. If that shape is absent, fall back to defensive alias matching for
    alternate/legacy payload shapes.
    """
    real: list[tuple[str, str, dict[str, Any]]] = []
    for block_key, child_key, canonical, default_label in _REAL_WINDOW_MAP:
        block = payload.get(block_key)
        if not isinstance(block, dict):
            continue
        window = block.get(child_key)
        if not isinstance(window, dict):
            continue
        label = str(
            window.get("name")
            or window.get("label")
            or default_label
        )
        real.append((canonical, label, window))
    if real:
        return real

    # Defensive fallback for alternate payload shapes.
    raw: dict[str, Any] = {}
    for candidate in ("rate_limits", "usage_windows", "windows", "quota_windows"):
        block = payload.get(candidate)
        if isinstance(block, dict):
            raw.update(block)
        elif isinstance(block, list):
            for item in block:
                if isinstance(item, dict):
                    key = str(item.get("id") or item.get("key") or item.get("name") or "")
                    if key:
                        raw[key] = item
    result: list[tuple[str, str, dict[str, Any]]] = []
    for canonical, aliases in _WINDOW_ALIASES.items():
        for alias in aliases:
            if alias in raw:
                window = raw[alias]
                if not isinstance(window, dict):
                    continue
                label = str(
                    window.get("name")
                    or window.get("label")
                    or _DEFAULT_LABELS.get(canonical, canonical)
                )
                result.append((canonical, label, window))
                break
    return result


_DEFAULT_LABELS = {
    "5h": "5 hour usage limit",
    "weekly": "weekly usage limit",
    "code_review": "code review usage limit",
}


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


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percent_from_counts(used: Any, limit: Any) -> int | None:
    u = _to_int(used)
    total = _to_int(limit)
    if u is None or total is None or total == 0:
        return None
    return max(0, min(100, round((u / total) * 100)))


def _resolve_account_id(auth: dict[str, Any]) -> str | None:
    """Resolve the ChatGPT account id from an auth file, mirroring AIUsage.

    Order: ``tokens.account_id`` -> ``tokens.accountId`` -> top-level
    ``account_id`` -> top-level ``accountId`` -> ``chatgpt_account_id`` claim
    decoded from the ``id_token`` JWT -> same claim from the ``access_token``.
    """
    tokens = auth.get("tokens") or {}
    for key in ("account_id", "accountId"):
        value = _str_or_none(tokens.get(key)) or _str_or_none(auth.get(key))
        if value:
            return value
    id_token = _str_or_none(tokens.get("id_token")) or _str_or_none(auth.get("id_token"))
    account_id = _account_id_from_jwt(id_token)
    if account_id:
        return account_id
    access_token = _str_or_none(tokens.get("access_token")) or _str_or_none(auth.get("access_token"))
    return _account_id_from_jwt(access_token)


def _account_id_from_jwt(token: str | None) -> str | None:
    """Extract ``chatgpt_account_id`` from a JWT's payload, OpenAI-style."""
    payload = _decode_jwt_payload(token)
    if not payload:
        return None
    auth_claim = payload.get("https://api.openai.com/auth")
    if isinstance(auth_claim, dict):
        return _str_or_none(auth_claim.get("chatgpt_account_id"))
    return None


def _decode_jwt_payload(token: str | None) -> dict[str, Any] | None:
    """Base64url-decode the payload segment of a JWT into a dict."""
    if not token or "." not in token:
        return None
    segments = token.split(".")
    if len(segments) < 2:
        return None
    payload = segments[1]
    # Pad to a multiple of 4 for base64.
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
    except (binascii.Error, ValueError):
        return None
    try:
        data = json.loads(decoded)
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _str_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _parse_expiry(value: Any) -> float | None:
    """Return an epoch seconds value from an expires_at field."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    dt = _parse_dt_value(value)
    if dt is not None:
        return dt.timestamp()
    return None


def _parse_dt_value(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _is_expired(expires_at: float | None) -> bool:
    if expires_at is None:
        # No expiry known; assume still valid to avoid needless refreshes.
        return False
    return time.time() + _REFRESH_SKEW_SECONDS >= expires_at


def _persist_tokens(
    path: Path, auth: dict[str, Any], new_tokens: dict[str, Any], device_id: str
) -> None:
    """Write refreshed tokens back to the mounted auth file."""
    tokens = auth.get("tokens") or {}
    tokens.update(
        {
            "access_token": new_tokens.get("access_token", tokens.get("access_token")),
            "refresh_token": new_tokens.get("refresh_token", tokens.get("refresh_token")),
            "id_token": new_tokens.get("id_token", tokens.get("id_token")),
        }
    )
    if "expires_in" in new_tokens:
        tokens["expires_at"] = time.time() + int(new_tokens["expires_in"])
    auth["tokens"] = tokens
    auth["last_refresh"] = utcnow().isoformat()
    auth["device_id"] = device_id
    try:
        path.write_text(json.dumps(auth, indent=2), encoding="utf-8")
    except OSError:
        # Read-only mounts are acceptable; we just won't persist the refresh.
        pass
