"""OpenCode Go API-key collector.

Reads an OpenCode Go auth file (``OPENCODE_GO_AUTH_FILE``) with the structure::

    {
      "opencode-go": { "type": "api", "key": "..." }
    }

Uses the API key to:

1. **Validate auth** against ``GET {base_url}/models``.
2. **Probe** for any documented or likely usage/balance endpoints.
3. **Normalize** any usage payload found into :class:`UsageMeter` rows.

If no usage endpoint is available, callers should fall back to the
Playwright/cookie scraping path (:mod:`app.providers.opencode_browser`).

The auth-file parsing function (:func:`read_go_auth_file`) is pure and has no
HTTP dependency, so it can be unit-tested in isolation. The HTTP functions
accept an injected ``httpx.AsyncClient`` so they are testable with
``MockTransport``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from ..models import UsageMeter, utcnow
from ..normalizer import compute_remaining, derive_status, format_reset_label, merge_metrics
from .base import ProviderError

log = logging.getLogger(__name__)

_ACCOUNT_ID = "opencode-go"

# The expected top-level key in the auth file.
_AUTH_KEY = "opencode-go"

# Candidate usage/balance endpoint paths (relative to the API base URL).
# Ordered by likelihood; the first that returns 200 with parseable JSON wins.
_CANDIDATE_USAGE_PATHS: tuple[str, ...] = (
    "/usage",
    "/balance",
    "/billing",
    "/quota",
    "/limits",
    "/user/usage",
    "/user/balance",
    "/me/usage",
    "/me/balance",
    "/subscription",
    "/account/usage",
    "/account/balance",
)

# Canonical meter keys -> label text, matching the Playwright collector.
_METER_KEYS: dict[str, str] = {
    "rolling": "Rolling Usage",
    "weekly": "Weekly Usage",
    "monthly": "Monthly Usage",
}


# --- Auth file parsing (pure) --------------------------------------------


def read_go_auth_file(path: Path) -> str:
    """Read an OpenCode Go auth file and return the API key.

    Expected structure::

        { "opencode-go": { "type": "api", "key": "sk-..." } }

    Raises :class:`ProviderError` if the file is missing, not valid JSON,
    missing the ``opencode-go`` entry, has the wrong type, or has no key.
    """
    path = Path(path)
    if not path.exists():
        raise ProviderError(f"OpenCode Go auth file not found: {path}")
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProviderError(f"OpenCode Go auth file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProviderError("OpenCode Go auth file must contain a JSON object")

    entry = data.get(_AUTH_KEY)
    if not isinstance(entry, dict):
        raise ProviderError(f"auth file missing '{_AUTH_KEY}' entry")

    entry_type = str(entry.get("type", "")).strip().lower()
    if entry_type != "api":
        raise ProviderError(
            f"'{_AUTH_KEY}' entry has type '{entry_type}', expected 'api'"
        )

    key = entry.get("key")
    if not isinstance(key, str) or not key.strip():
        raise ProviderError(f"'{_AUTH_KEY}' entry missing 'key' field")
    return key.strip()


# --- API validation + usage probing --------------------------------------


async def validate_auth(
    client: httpx.AsyncClient, base_url: str, api_key: str
) -> bool:
    """Validate the API key against ``GET {base_url}/models``.

    Returns ``True`` if the endpoint responds 200, ``False`` otherwise.
    Network/parse errors are logged and return ``False`` (treated as
    auth-invalid) rather than raising, so the caller can produce a clean
    error meter.
    """
    url = f"{base_url.rstrip('/')}/models"
    headers = _auth_headers(api_key)
    try:
        resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        log.warning("OpenCode Go auth validation request failed: %s", exc)
        return False
    if resp.status_code == 401 or resp.status_code == 403:
        log.warning("OpenCode Go auth validation returned HTTP %s (key rejected)", resp.status_code)
        return False
    if resp.status_code >= 400:
        log.warning("OpenCode Go auth validation returned HTTP %s", resp.status_code)
        return False
    return True


async def probe_usage_endpoints(
    client: httpx.AsyncClient, base_url: str, api_key: str
) -> dict[str, Any] | None:
    """Probe likely usage/balance endpoints and return the first payload found.

    Iterates ``_CANDIDATE_USAGE_PATHS``; the first endpoint that returns 200
    with a JSON dict body wins. Returns ``None`` if no endpoint yields data.
    """
    headers = _auth_headers(api_key)
    base = base_url.rstrip("/")
    for path in _CANDIDATE_USAGE_PATHS:
        url = f"{base}{path}"
        try:
            resp = await client.get(url, headers=headers)
        except httpx.HTTPError:
            continue
        if resp.status_code != 200:
            continue
        try:
            body = resp.json()
        except ValueError:
            continue
        if isinstance(body, dict):
            log.info("OpenCode Go usage data found at %s", path)
            return body
    log.info("no OpenCode Go usage endpoint found among %d candidates", len(_CANDIDATE_USAGE_PATHS))
    return None


def normalize_usage(payload: dict[str, Any], label: str) -> list[UsageMeter]:
    """Normalize an OpenCode Go usage payload into meters.

    Defensive parsing: tolerates a few response shapes — windows keyed by
    ``rolling``/``weekly``/``monthly`` (with ``used_percent`` + optional
    ``reset_at``), or a flat object with ``used_percent``.
    """
    now = utcnow()
    meters: list[UsageMeter] = []

    # Shape A: keyed windows (e.g. { "rolling": { "used_percent": 2, ... }, ... })
    found_keyed = False
    for key, default_label in _METER_KEYS.items():
        window = payload.get(key)
        if isinstance(window, dict):
            found_keyed = True
            meter = _window_to_meter(key, default_label, window, label, now)
            if meter is not None:
                meters.append(meter)

    if found_keyed:
        return meters

    # Shape B: flat usage object with used_percent (single "monthly" meter).
    if "used_percent" in payload:
        meter = _window_to_meter("monthly", _METER_KEYS["monthly"], payload, label, now)
        if meter is not None:
            meters.append(meter)
        return meters

    # Shape C: a top-level "usage" or "data" block containing keyed windows.
    for wrapper_key in ("usage", "data", "quota", "balance"):
        block = payload.get(wrapper_key)
        if isinstance(block, dict):
            return normalize_usage(block, label)

    return meters


def _window_to_meter(
    key: str, default_label: str, window: dict[str, Any], account_label: str, now: datetime
) -> UsageMeter | None:
    """Build a single :class:`UsageMeter` from a window dict."""
    used_pct = _to_int(window.get("used_percent"))
    if used_pct is None:
        # Try used/limit counts if used_percent is absent.
        used_pct = _percent_from_counts(window.get("used"), window.get("limit"))
    remaining_pct = compute_remaining(used_pct, None)
    reset_at = _parse_dt(window.get("reset_at"))
    label = str(window.get("label") or window.get("name") or default_label)
    return UsageMeter(
        id=f"{_ACCOUNT_ID}-{key}",
        provider="opencode",
        account_id=_ACCOUNT_ID,
        account_label=account_label,
        label=label,
        used_percent=used_pct,
        remaining_percent=remaining_pct,
        reset_at=reset_at,
        reset_label=format_reset_label(reset_at, now) if reset_at else window.get("reset_label"),
        status=derive_status(remaining_pct),
        updated_at=now,
        metrics=merge_metrics(
            used=_to_float(window.get("used")),
            limit=_to_float(window.get("limit")),
            unit=window.get("unit"),
            cost_used=_to_float(window.get("cost_used")),
            cost_limit=_to_float(window.get("cost_limit")),
            balance=_to_float(window.get("balance")),
            currency=window.get("currency"),
        ),
    )


# --- Helpers --------------------------------------------------------------


def _auth_headers(api_key: str) -> dict[str, str]:
    """Build standard auth headers for OpenCode Go API requests."""
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
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


def _parse_dt(value: Any) -> datetime | None:
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
