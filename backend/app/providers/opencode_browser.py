"""Playwright browser fetcher for the OpenCode Go dashboard.

Uses a **persistent** Chromium context (``launch_persistent_context``) so the
one-time interactive login session survives across refresh runs and container
restarts. The profile directory is mounted as a Docker volume.

Optionally loads a Cookie-Editor JSON export (``OPENCODE_COOKIES_FILE``) before
navigating, so auth can be seeded without an interactive login.

Playwright is imported lazily so that ``static`` mode (and the test suite) does
not require the ``playwright`` package or browser binaries to be installed. The
cookie conversion functions are pure and have no Playwright dependency, so they
can be unit-tested in isolation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .base import ProviderError

log = logging.getLogger(__name__)

# Default navigation wait. The dashboard may need to fetch usage data after
# the initial paint, so "networkidle" gives it room to settle.
_DEFAULT_TIMEOUT_MS = 30_000

# Cookie-Editor sameSite values -> Playwright sameSite enum.
# Cookie-Editor uses lowercase: "lax", "strict", "no", "unspecified".
# Playwright expects: "Lax", "Strict", "None".
_SAMESITE_MAP: dict[str, str] = {
    "lax": "Lax",
    "strict": "Strict",
    "no": "None",
    "none": "None",
    "unspecified": "Lax",  # browser default for unspecified SameSite
}


def convert_cookie_editor_cookies(raw_cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert a Cookie-Editor JSON export into Playwright cookie dicts.

    Cookie-Editor fields -> Playwright fields:
        name, value, domain, path, httpOnly, secure  -> kept as-is
        expirationDate                               -> expires (float; -1 if absent)
        sameSite                                     -> normalized to Lax/Strict/None

    Domains without a leading dot (the common Cookie-Editor format) are
    accepted as-is; Playwright handles both ``example.com`` and
    ``.example.com``.
    """
    converted: list[dict[str, Any]] = []
    for raw in raw_cookies:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        value = raw.get("value")
        if name is None or value is None:
            continue  # skip malformed entries

        cookie: dict[str, Any] = {
            "name": str(name),
            "value": str(value),
            "domain": str(raw.get("domain", "")),
            "path": str(raw.get("path", "/")),
            "httpOnly": bool(raw.get("httpOnly", False)),
            "secure": bool(raw.get("secure", False)),
            "sameSite": _normalize_samesite(raw.get("sameSite")),
            "expires": _to_expires(raw.get("expirationDate")),
        }
        converted.append(cookie)
    return converted


def load_cookies_from_file(path: Path) -> list[dict[str, Any]]:
    """Read a Cookie-Editor JSON export from ``path`` and convert it.

    Raises :class:`ProviderError` if the file is missing, not valid JSON, or
    not a list.
    """
    path = Path(path)
    if not path.exists():
        raise ProviderError(f"cookies file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProviderError(f"cookies file is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ProviderError("cookies file must contain a JSON array of cookies")
    return convert_cookie_editor_cookies(data)


def _normalize_samesite(value: Any) -> str:
    """Map a Cookie-Editor sameSite value to Playwright's enum."""
    if value is None:
        return "Lax"
    key = str(value).strip().lower()
    return _SAMESITE_MAP.get(key, "Lax")


def _to_expires(expiration_date: Any) -> float:
    """Convert Cookie-Editor ``expirationDate`` to Playwright ``expires``.

    Cookie-Editor stores a Unix timestamp in seconds (float). Session cookies
    have no ``expirationDate``; Playwright uses ``-1`` for session cookies.
    """
    if expiration_date is None:
        return -1
    try:
        return float(expiration_date)
    except (TypeError, ValueError):
        return -1


async def fetch_dashboard_html(
    url: str,
    profile_dir: Path,
    headless: bool = True,
    timeout_ms: int = _DEFAULT_TIMEOUT_MS,
    cookies_file: Path | None = None,
) -> str:
    """Visit the OpenCode Go dashboard with a persistent Chromium context.

    If ``cookies_file`` is provided, the cookies are loaded and added to the
    context **before** navigating, so the dashboard sees an authenticated
    session.

    Returns the page's HTML after the dashboard settles. Raises
    :class:`ProviderError` on any failure so the adapter can record an error
    meter.
    """
    if not url:
        raise ProviderError("OPENCODE_DASHBOARD_URL is not set")

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise ProviderError(
            "playwright is not installed; run `pip install playwright` "
            "and `playwright install chromium`"
        ) from exc

    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    # Load cookies before opening the browser so conversion errors surface early.
    cookies: list[dict[str, Any]] = []
    if cookies_file is not None:
        cookies = load_cookies_from_file(cookies_file)
        log.info("loaded %d cookies from %s", len(cookies), cookies_file)

    log.info("opening OpenCode dashboard (headless=%s): %s", headless, url)
    try:
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                if cookies:
                    await context.add_cookies(cookies)
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                # Give late-rendering usage cards a moment to appear.
                await page.wait_for_timeout(1500)
                html = await page.content()
                return html
            finally:
                await context.close()
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"playwright fetch failed: {exc}") from exc
