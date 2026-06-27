"""Copilot adapter tests using httpx MockTransport and sanitized fixtures."""

from __future__ import annotations

import httpx
import pytest

from app.providers.copilot import CopilotAdapter
from tests.conftest import load_fixture, make_settings


def _mock_transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_copilot_normalizes_snapshots(db_path: Path):
    settings = make_settings(db_path, copilot_token="ghp_testtoken")
    adapter = CopilotAdapter(settings)
    payload = load_fixture("copilot_user.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "copilot_internal/user" in str(request.url)
        assert request.headers["Authorization"] == "token ghp_testtoken"
        return httpx.Response(200, json=payload)

    adapter._client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        meters = await adapter.fetch_meters()
    finally:
        await adapter.aclose()

    assert len(meters) == 2
    ids = {m.id for m in meters}
    assert "copilot-personal-code_completions" in ids
    assert "copilot-personal-chat" in ids

    cc = next(m for m in meters if m.id.endswith("-code_completions"))
    assert cc.used_percent == 35  # 35/100
    assert cc.remaining_percent == 65
    assert cc.status == "ok"
    assert cc.metrics.tokens_used == 35
    assert cc.metrics.tokens_limit == 100

    chat = next(m for m in meters if m.id.endswith("-chat"))
    assert chat.used_percent == 40  # 200/500
    assert chat.remaining_percent == 60


@pytest.mark.asyncio
async def test_copilot_http_error(db_path: Path):
    settings = make_settings(db_path, copilot_token="ghp_testtoken")
    adapter = CopilotAdapter(settings)

    adapter._client = httpx.AsyncClient(transport=_mock_transport(lambda r: httpx.Response(403)))
    try:
        meters = await adapter.fetch_meters()
    finally:
        await adapter.aclose()
    assert len(meters) == 1
    assert meters[0].status == "error"


@pytest.mark.asyncio
async def test_copilot_disabled_without_token(settings):
    adapter = CopilotAdapter(settings)
    assert not adapter.enabled
    assert await adapter.fetch_meters() == []

@pytest.mark.asyncio
async def test_copilot_normalizes_current_dict_snapshots(db_path: Path):
    settings = make_settings(db_path, copilot_token="ghp_testtoken")
    adapter = CopilotAdapter(settings)
    payload = {
        "quota_snapshots": {
            "chat": {
                "percent_remaining": 88.5,
                "quota_id": "chat",
                "has_quota": True,
                "remaining": 177,
                "entitlement": 200,
            },
            "completions": {
                "percent_remaining": 96.4,
                "quota_id": "completions",
                "has_quota": True,
                "remaining": 1929,
                "entitlement": 2000,
            },
            "premium_interactions": {
                "percent_remaining": 0.0,
                "quota_id": "premium_interactions",
                "has_quota": False,
                "remaining": 0,
                "entitlement": 0,
            },
        },
        "quota_reset_date_utc": "2026-07-01T00:00:00.000Z",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    adapter._client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        meters = await adapter.fetch_meters()
    finally:
        await adapter.aclose()

    assert {m.id for m in meters} == {"copilot-personal-chat", "copilot-personal-completions"}
    chat = next(m for m in meters if m.id.endswith("-chat"))
    assert chat.used_percent == 12
    assert chat.remaining_percent == 88
    assert chat.metrics.tokens_used == 23
    assert chat.metrics.tokens_limit == 200
    assert chat.reset_at.isoformat() == "2026-07-01T00:00:00+00:00"
