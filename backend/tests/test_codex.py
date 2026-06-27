"""Codex adapter tests using httpx MockTransport and sanitized fixtures."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.providers.codex import CodexAdapter
from tests.conftest import load_fixture, make_settings


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_jwt(payload: dict) -> str:
    """Build an unsigned JWT whose payload decodes to ``payload``."""
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    body = _b64url(json.dumps(payload).encode())
    return f"{header}.{body}."


def _write_auth(path: Path, *, access_token: str = "valid-token", refresh_token: str = "r", client_id: str = "app_test") -> None:
    path.write_text(
        json.dumps(
            {
                "client_id": client_id,
                "tokens": {"access_token": access_token, "refresh_token": refresh_token},
                "device_id": "device-test",
            }
        ),
        encoding="utf-8",
    )


def _write_auth_tokens(path: Path, tokens: dict, **extra) -> None:
    """Write an auth file with an arbitrary ``tokens`` block plus extra fields."""
    payload = {"device_id": "device-test", "tokens": tokens}
    payload.update(extra)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.asyncio
async def test_codex_normalizes_windows(db_path: Path, tmp_path: Path, monkeypatch):
    auth_file = tmp_path / "auth.json"
    _write_auth(auth_file)
    monkeypatch.setenv("CODEX_PERSONAL_AUTH_FILE", str(auth_file))

    settings = make_settings(db_path, codex_accounts="personal")
    adapter = CodexAdapter(settings)

    usage = load_fixture("codex_wham_usage.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/wham/usage")
        assert request.headers["Authorization"] == "Bearer valid-token"
        return httpx.Response(200, json=usage)

    adapter._client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        meters = await adapter.fetch_meters()
    finally:
        await adapter.aclose()

    assert len(meters) == 3
    ids = {m.id for m in meters}
    assert "codex-personal-5h" in ids
    assert "codex-personal-weekly" in ids
    assert "codex-personal-code_review" in ids

    five = next(m for m in meters if m.id.endswith("-5h"))
    assert five.provider == "codex"
    assert five.account_id == "codex-personal"
    assert five.used_percent == 2
    assert five.remaining_percent == 98
    assert five.status == "ok"
    assert five.reset_at is not None
    assert five.reset_label is not None

    review = next(m for m in meters if m.id.endswith("-code_review"))
    # used=5, limit=50 -> 10% used -> 90% remaining
    assert review.used_percent == 10
    assert review.remaining_percent == 90


@pytest.mark.asyncio
async def test_codex_refreshes_expired_token(db_path: Path, tmp_path: Path, monkeypatch):
    auth_file = tmp_path / "auth.json"
    # No access_token -> forces refresh.
    auth_file.write_text(
        json.dumps(
            {
                "client_id": "app_test",
                "tokens": {"refresh_token": "old-refresh"},
                "device_id": "device-test",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_PERSONAL_AUTH_FILE", str(auth_file))

    settings = make_settings(db_path, codex_accounts="personal")
    adapter = CodexAdapter(settings)
    usage = load_fixture("codex_wham_usage.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth/token" in str(request.url):
            body = json.loads(request.content.decode())
            assert body["grant_type"] == "refresh_token"
            assert body["refresh_token"] == "old-refresh"
            return httpx.Response(
                200,
                json={"access_token": "new-token", "refresh_token": "new-refresh", "expires_in": 3600},
            )
        assert request.headers["Authorization"] == "Bearer new-token"
        return httpx.Response(200, json=usage)

    adapter._client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        meters = await adapter.fetch_meters()
    finally:
        await adapter.aclose()

    assert len(meters) == 3
    persisted = json.loads(auth_file.read_text(encoding="utf-8"))
    assert persisted["tokens"]["access_token"] == "new-token"
    assert "expires_at" in persisted["tokens"]


@pytest.mark.asyncio
async def test_codex_missing_auth_file(db_path: Path, monkeypatch):
    monkeypatch.setenv("CODEX_PERSONAL_AUTH_FILE", "/nonexistent/path.json")
    settings = make_settings(db_path, codex_accounts="personal")
    adapter = CodexAdapter(settings)
    adapter._client = httpx.AsyncClient(transport=_mock_transport(lambda r: httpx.Response(200, json={})))
    try:
        meters = await adapter.fetch_meters()
    finally:
        await adapter.aclose()
    assert len(meters) == 1
    assert meters[0].status == "error"
    assert "auth file not found" in meters[0].reset_label


@pytest.mark.asyncio
async def test_codex_disabled_when_no_accounts(settings):
    adapter = CodexAdapter(settings)
    assert not adapter.enabled
    assert await adapter.fetch_meters() == []


# --- Real AIUsage wham/usage shape ------------------------------------------


@pytest.mark.asyncio
async def test_codex_real_wham_shape(db_path: Path, tmp_path: Path, monkeypatch):
    """rate_limit.primary_window / secondary_window + code_review_rate_limit."""
    auth_file = tmp_path / "auth.json"
    _write_auth(auth_file)
    monkeypatch.setenv("CODEX_PERSONAL_AUTH_FILE", str(auth_file))

    settings = make_settings(db_path, codex_accounts="personal")
    adapter = CodexAdapter(settings)
    usage = load_fixture("codex_wham_usage_real.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/wham/usage")
        return httpx.Response(200, json=usage)

    adapter._client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        meters = await adapter.fetch_meters()
    finally:
        await adapter.aclose()

    assert len(meters) == 3
    by_id = {m.id: m for m in meters}
    assert set(by_id) == {
        "codex-personal-5h",
        "codex-personal-weekly",
        "codex-personal-code_review",
    }

    five = by_id["codex-personal-5h"]
    assert five.label == "5 hour usage limit"
    assert five.used_percent == 2          # 2.0 -> int
    assert five.remaining_percent == 98    # 100 - used (no remaining_percent in payload)
    assert five.status == "ok"
    assert five.reset_at == datetime(2027, 6, 27, 22, 58, tzinfo=timezone.utc)
    assert five.reset_label is not None

    weekly = by_id["codex-personal-weekly"]
    assert weekly.label == "weekly usage limit"
    assert weekly.used_percent == 12
    assert weekly.remaining_percent == 88
    assert weekly.reset_at == datetime(2027, 6, 28, 18, 46, tzinfo=timezone.utc)

    review = by_id["codex-personal-code_review"]
    assert review.label == "code review usage limit"
    assert review.used_percent == 5
    assert review.remaining_percent == 95
    assert review.reset_at == datetime(2027, 6, 27, 22, 58, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_codex_sends_account_id_header(db_path: Path, tmp_path: Path, monkeypatch):
    """ChatGPT-Account-Id is sent when the auth file carries an account id."""
    auth_file = tmp_path / "auth.json"
    _write_auth_tokens(
        auth_file,
        {"access_token": "valid-token", "account_id": "acct_personal"},
    )
    monkeypatch.setenv("CODEX_PERSONAL_AUTH_FILE", str(auth_file))

    settings = make_settings(db_path, codex_accounts="personal")
    adapter = CodexAdapter(settings)
    usage = load_fixture("codex_wham_usage_real.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "x"})
        assert request.headers["ChatGPT-Account-Id"] == "acct_personal"
        return httpx.Response(200, json=usage)

    adapter._client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        meters = await adapter.fetch_meters()
    finally:
        await adapter.aclose()
    assert len(meters) == 3


@pytest.mark.asyncio
async def test_codex_no_account_id_header_when_absent(db_path: Path, tmp_path: Path, monkeypatch):
    """No ChatGPT-Account-Id header when the auth file has no resolvable id."""
    auth_file = tmp_path / "auth.json"
    _write_auth(auth_file)  # no account_id, no JWT
    monkeypatch.setenv("CODEX_PERSONAL_AUTH_FILE", str(auth_file))

    settings = make_settings(db_path, codex_accounts="personal")
    adapter = CodexAdapter(settings)
    usage = load_fixture("codex_wham_usage_real.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "ChatGPT-Account-Id" not in request.headers
        return httpx.Response(200, json=usage)

    adapter._client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        await adapter.fetch_meters()
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_codex_account_id_resolved_from_jwt(db_path: Path, tmp_path: Path, monkeypatch):
    """Account id falls back to the chatgpt_account_id claim in the id_token JWT."""
    jwt_payload = {
        "sub": "user-123",
        "https://api.openai.com/auth": {"chatgpt_account_id": "jwt-acct"},
    }
    auth_file = tmp_path / "auth.json"
    _write_auth_tokens(
        auth_file,
        {"access_token": "valid-token", "id_token": _make_jwt(jwt_payload)},
    )
    monkeypatch.setenv("CODEX_PERSONAL_AUTH_FILE", str(auth_file))

    settings = make_settings(db_path, codex_accounts="personal")
    adapter = CodexAdapter(settings)
    usage = load_fixture("codex_wham_usage_real.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "x"})
        assert request.headers["ChatGPT-Account-Id"] == "jwt-acct"
        return httpx.Response(200, json=usage)

    adapter._client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        await adapter.fetch_meters()
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_codex_static_oauth_client_id_fallback(db_path: Path, tmp_path: Path, monkeypatch):
    """Refresh uses the static Codex CLI client id when the auth file omits one."""
    auth_file = tmp_path / "auth.json"
    # No access_token -> forces refresh; no client_id -> must fall back to static id.
    _write_auth_tokens(auth_file, {"refresh_token": "old-refresh"})
    monkeypatch.setenv("CODEX_PERSONAL_AUTH_FILE", str(auth_file))

    settings = make_settings(db_path, codex_accounts="personal")
    adapter = CodexAdapter(settings)
    usage = load_fixture("codex_wham_usage_real.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth/token" in str(request.url):
            body = json.loads(request.content.decode())
            assert body["client_id"] == "app_EMoamEEZ73f0CkXaXp7hrann"
            return httpx.Response(
                200,
                json={"access_token": "new-token", "refresh_token": "new-refresh", "expires_in": 3600},
            )
        assert request.headers["Authorization"] == "Bearer new-token"
        return httpx.Response(200, json=usage)

    adapter._client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        meters = await adapter.fetch_meters()
    finally:
        await adapter.aclose()
    assert len(meters) == 3
