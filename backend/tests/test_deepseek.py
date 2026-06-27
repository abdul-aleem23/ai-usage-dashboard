"""DeepSeek adapter tests."""

from __future__ import annotations

import httpx
import pytest

from app.providers.deepseek import DeepSeekAdapter
from tests.conftest import load_fixture, make_settings


def _mock_transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_deepseek_normalizes_balance(db_path: Path):
    settings = make_settings(db_path, deepseek_api_key="sk-test", deepseek_balance_target_usd=20.0, deepseek_low_balance_usd=1.0)
    adapter = DeepSeekAdapter(settings)
    payload = load_fixture("deepseek_balance.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/user/balance")
        assert request.headers["Authorization"] == "Bearer sk-test"
        return httpx.Response(200, json=payload)

    adapter._client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        meters = await adapter.fetch_meters()
    finally:
        await adapter.aclose()

    assert len(meters) == 2
    currencies = {m.metrics.currency for m in meters}
    assert currencies == {"CNY", "USD"}
    cny = next(m for m in meters if m.metrics.currency == "CNY")
    assert cny.metrics.balance == 10.50
    assert cny.remaining_percent is None
    assert cny.status == "ok"

    usd = next(m for m in meters if m.metrics.currency == "USD")
    assert usd.metrics.balance == 1.25
    assert usd.metrics.cost_limit == 20.0
    assert usd.remaining_percent == 6
    assert usd.used_percent == 94
    assert usd.reset_label == "1.25 / 20.00 USD"
    assert usd.status == "ok"


@pytest.mark.asyncio
async def test_deepseek_http_error(db_path: Path):
    settings = make_settings(db_path, deepseek_api_key="sk-test", deepseek_balance_target_usd=20.0, deepseek_low_balance_usd=1.0)
    adapter = DeepSeekAdapter(settings)
    adapter._client = httpx.AsyncClient(transport=_mock_transport(lambda r: httpx.Response(401)))
    try:
        meters = await adapter.fetch_meters()
    finally:
        await adapter.aclose()
    assert len(meters) == 1
    assert meters[0].status == "error"


@pytest.mark.asyncio
async def test_deepseek_disabled_without_key(settings):
    adapter = DeepSeekAdapter(settings)
    assert not adapter.enabled
    assert await adapter.fetch_meters() == []

@pytest.mark.asyncio
async def test_deepseek_low_usd_balance_warns(db_path: Path):
    settings = make_settings(
        db_path,
        deepseek_api_key="sk-test",
        deepseek_balance_target_usd=5.0,
        deepseek_low_balance_usd=1.0,
    )
    adapter = DeepSeekAdapter(settings)
    payload = {"balance_infos": [{"currency": "USD", "total_balance": "0.71"}]}

    adapter._client = httpx.AsyncClient(
        transport=_mock_transport(lambda r: httpx.Response(200, json=payload))
    )
    try:
        meters = await adapter.fetch_meters()
    finally:
        await adapter.aclose()

    assert len(meters) == 1
    assert meters[0].remaining_percent == 14
    assert meters[0].used_percent == 86
    assert meters[0].status == "warning"
    assert meters[0].reset_label == "0.71 / 5.00 USD"