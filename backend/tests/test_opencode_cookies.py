"""Cookie conversion tests for the OpenCode Playwright collector.

These tests exercise the pure :func:`convert_cookie_editor_cookies` and
:func:`load_cookies_from_file` functions in ``opencode_browser`` — no
Playwright, no browser, no network. The browser fetcher itself is covered
separately by integration testing at deploy time.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.providers.base import ProviderError
from app.providers.opencode_browser import convert_cookie_editor_cookies, load_cookies_from_file

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_raw_cookies() -> list[dict]:
    return json.loads((FIXTURES_DIR / "opencode_cookies.json").read_text(encoding="utf-8"))


# --- Conversion from fixture ----------------------------------------------


def test_convert_preserves_name_value_domain_path():
    raw = _load_raw_cookies()
    cookies = convert_cookie_editor_cookies(raw)

    session = next(c for c in cookies if c["name"] == "session_id")
    assert session["value"] == "s_abc123def456"
    assert session["domain"] == "opencode.example.com"  # no leading dot, kept as-is
    assert session["path"] == "/"

    csrf = next(c for c in cookies if c["name"] == "csrf_token")
    assert csrf["domain"] == ".opencode.example.com"  # leading dot, kept as-is
    assert csrf["path"] == "/dashboard"


def test_convert_expiration_date_to_expires():
    raw = _load_raw_cookies()
    cookies = convert_cookie_editor_cookies(raw)

    session = next(c for c in cookies if c["name"] == "session_id")
    assert session["expires"] == 1814137080.0

    # Float expirationDate preserved as float.
    csrf = next(c for c in cookies if c["name"] == "csrf_token")
    assert csrf["expires"] == 1814137080.5


def test_convert_session_cookie_without_expiration_date():
    raw = _load_raw_cookies()
    cookies = convert_cookie_editor_cookies(raw)

    theme = next(c for c in cookies if c["name"] == "theme")
    # No expirationDate in the raw cookie -> session cookie -> expires = -1.
    assert theme["expires"] == -1


def test_convert_sameSite_normalization():
    raw = _load_raw_cookies()
    cookies = convert_cookie_editor_cookies(raw)

    by_name = {c["name"]: c for c in cookies}
    # lax -> Lax
    assert by_name["session_id"]["sameSite"] == "Lax"
    # strict -> Strict
    assert by_name["csrf_token"]["sameSite"] == "Strict"
    # no -> None
    assert by_name["theme"]["sameSite"] == "None"
    # unspecified -> Lax (browser default)
    assert by_name["analytics_id"]["sameSite"] == "Lax"


def test_convert_preserves_httponly_and_secure():
    raw = _load_raw_cookies()
    cookies = convert_cookie_editor_cookies(raw)

    session = next(c for c in cookies if c["name"] == "session_id")
    assert session["httpOnly"] is True
    assert session["secure"] is True

    theme = next(c for c in cookies if c["name"] == "theme")
    assert theme["httpOnly"] is False
    assert theme["secure"] is False


def test_convert_all_cookies_from_fixture():
    raw = _load_raw_cookies()
    cookies = convert_cookie_editor_cookies(raw)
    assert len(cookies) == 4


# --- Edge cases ----------------------------------------------------------


def test_convert_empty_list():
    assert convert_cookie_editor_cookies([]) == []


def test_convert_skips_malformed_entries():
    raw = [
        {"name": "ok", "value": "v", "domain": "x.com"},
        {"name": "no_value", "domain": "x.com"},  # missing value -> skipped
        {"value": "no_name", "domain": "x.com"},  # missing name -> skipped
        "not-a-dict",  # not a dict -> skipped
    ]
    cookies = convert_cookie_editor_cookies(raw)
    assert len(cookies) == 1
    assert cookies[0]["name"] == "ok"


def test_convert_defaults_path_to_slash():
    cookies = convert_cookie_editor_cookies([{"name": "a", "value": "b", "domain": "x.com"}])
    assert cookies[0]["path"] == "/"


def test_convert_defaults_httponly_and_secure_to_false():
    cookies = convert_cookie_editor_cookies([{"name": "a", "value": "b", "domain": "x.com"}])
    assert cookies[0]["httpOnly"] is False
    assert cookies[0]["secure"] is False


def test_convert_defaults_samesite_to_lax_when_missing():
    cookies = convert_cookie_editor_cookies([{"name": "a", "value": "b", "domain": "x.com"}])
    assert cookies[0]["sameSite"] == "Lax"


def test_convert_defaults_expires_to_minus_one_when_missing():
    cookies = convert_cookie_editor_cookies([{"name": "a", "value": "b", "domain": "x.com"}])
    assert cookies[0]["expires"] == -1


def test_convert_invalid_expiration_date_becomes_session_cookie():
    raw = [{"name": "a", "value": "b", "domain": "x.com", "expirationDate": "not-a-number"}]
    cookies = convert_cookie_editor_cookies(raw)
    assert cookies[0]["expires"] == -1


def test_convert_domain_without_leading_dot():
    """Domains without a leading dot are accepted as-is (not rejected)."""
    cookies = convert_cookie_editor_cookies(
        [{"name": "a", "value": "b", "domain": "opencode.example.com", "path": "/"}]
    )
    assert cookies[0]["domain"] == "opencode.example.com"


def test_convert_sameSite_case_insensitive():
    raw = [
        {"name": "a", "value": "b", "domain": "x.com", "sameSite": "LAX"},
        {"name": "c", "value": "d", "domain": "x.com", "sameSite": "Strict"},
        {"name": "e", "value": "f", "domain": "x.com", "sameSite": "NONE"},
    ]
    cookies = convert_cookie_editor_cookies(raw)
    assert cookies[0]["sameSite"] == "Lax"
    assert cookies[1]["sameSite"] == "Strict"
    assert cookies[2]["sameSite"] == "None"


# --- load_cookies_from_file -----------------------------------------------


def test_load_cookies_from_file(tmp_path: Path):
    raw = _load_raw_cookies()
    path = tmp_path / "cookies.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    cookies = load_cookies_from_file(path)
    assert len(cookies) == 4
    assert cookies[0]["name"] == "session_id"
    assert cookies[0]["expires"] == 1814137080.0


def test_load_cookies_missing_file(tmp_path: Path):
    with pytest.raises(ProviderError, match="not found"):
        load_cookies_from_file(tmp_path / "nope.json")


def test_load_cookies_invalid_json(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ProviderError, match="not valid JSON"):
        load_cookies_from_file(path)


def test_load_cookies_not_a_list(tmp_path: Path):
    path = tmp_path / "obj.json"
    path.write_text(json.dumps({"name": "a"}), encoding="utf-8")
    with pytest.raises(ProviderError, match="JSON array"):
        load_cookies_from_file(path)


def test_load_cookies_from_real_fixture():
    """End-to-end: load the actual fixture file through the loader."""
    cookies = load_cookies_from_file(FIXTURES_DIR / "opencode_cookies.json")
    assert len(cookies) == 4
    names = {c["name"] for c in cookies}
    assert names == {"session_id", "csrf_token", "theme", "analytics_id"}
