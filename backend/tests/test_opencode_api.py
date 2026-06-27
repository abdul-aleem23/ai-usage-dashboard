"""OpenCode Go API-key collector tests.

Covers:
* Auth-file parsing (pure, no HTTP).
* API validation against /models (httpx MockTransport).
* Usage endpoint probing (httpx MockTransport).
* Usage payload normalization (pure).
* Adapter-level ``api`` mode dispatch.

No live network calls; all HTTP is mocked via ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.providers.base import ProviderError
from app.providers.opencode_api import (
    normalize_usage,
    probe_usage_endpoints,
    read_go_auth_file,
    validate_auth,
)
from tests.conftest import load_fixture

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _mock_transport(handler):
    return httpx.MockTransport(handler)


# --- Auth file parsing (pure) --------------------------------------------


def test_read_auth_file_returns_key(tmp_path: Path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"opencode-go": {"type": "api", "key": "sk-test-key-123"}}),
        encoding="utf-8",
    )
    key = read_go_auth_file(auth_path)
    assert key == "sk-test-key-123"


def test_read_auth_file_from_real_fixture():
    """The redacted fixture should parse and return the (redacted) key."""
    key = read_go_auth_file(FIXTURES_DIR / "opencode_go_auth.json")
    assert key.startswith("sk-go-REDACTED")


def test_read_auth_file_missing_file(tmp_path: Path):
    with pytest.raises(ProviderError, match="not found"):
        read_go_auth_file(tmp_path / "nope.json")


def test_read_auth_file_invalid_json(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProviderError, match="not valid JSON"):
        read_go_auth_file(path)


def test_read_auth_file_not_object(tmp_path: Path):
    path = tmp_path / "arr.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ProviderError, match="JSON object"):
        read_go_auth_file(path)


def test_read_auth_file_missing_opencode_go_entry(tmp_path: Path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"other-account": {"type": "api", "key": "x"}}), encoding="utf-8")
    with pytest.raises(ProviderError, match="missing 'opencode-go'"):
        read_go_auth_file(path)


def test_read_auth_file_wrong_type(tmp_path: Path):
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps({"opencode-go": {"type": "oauth", "key": "x"}}),
        encoding="utf-8",
    )
    with pytest.raises(ProviderError, match="expected 'api'"):
        read_go_auth_file(path)


def test_read_auth_file_missing_key(tmp_path: Path):
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps({"opencode-go": {"type": "api"}}),
        encoding="utf-8",
    )
    with pytest.raises(ProviderError, match="missing 'key'"):
        read_go_auth_file(path)


def test_read_auth_file_empty_key(tmp_path: Path):
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps({"opencode-go": {"type": "api", "key": "  "}}),
        encoding="utf-8",
    )
    with pytest.raises(ProviderError, match="missing 'key'"):
        read_go_auth_file(path)


# --- API validation ------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_auth_success():
    models = load_fixture("opencode_models.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        assert request.headers["Authorization"] == "Bearer sk-test"
        return httpx.Response(200, json=models)

    client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        assert await validate_auth(client, "https://opencode.ai/zen/go/v1", "sk-test") is True
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_validate_auth_rejected_401():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_api_key"})

    client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        assert await validate_auth(client, "https://opencode.ai/zen/go/v1", "bad-key") is False
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_validate_auth_rejected_403():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        assert await validate_auth(client, "https://opencode.ai/zen/go/v1", "bad-key") is False
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_validate_auth_network_error_returns_false():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        assert await validate_auth(client, "https://opencode.ai/zen/go/v1", "sk-test") is False
    finally:
        await client.aclose()


# --- Usage endpoint probing ----------------------------------------------


@pytest.mark.asyncio
async def test_probe_finds_usage_endpoint():
    usage = load_fixture("opencode_usage.json")

    def handler(request: httpx.Request) -> httpx.Response:
        # /models -> 200 (not relevant here), /usage -> 200 with data
        if request.url.path.endswith("/usage"):
            return httpx.Response(200, json=usage)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        payload = await probe_usage_endpoints(client, "https://opencode.ai/zen/go/v1", "sk-test")
    finally:
        await client.aclose()
    assert payload is not None
    assert "rolling" in payload


@pytest.mark.asyncio
async def test_probe_returns_none_when_no_endpoint_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        payload = await probe_usage_endpoints(client, "https://opencode.ai/zen/go/v1", "sk-test")
    finally:
        await client.aclose()
    assert payload is None


@pytest.mark.asyncio
async def test_probe_skips_non_200_and_non_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/usage"):
            return httpx.Response(500)  # server error -> skip
        if path.endswith("/balance"):
            return httpx.Response(200, json=["not", "a", "dict"])  # list -> skip
        if path.endswith("/quota"):
            return httpx.Response(200, json={"used_percent": 50})  # dict -> win
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        payload = await probe_usage_endpoints(client, "https://opencode.ai/zen/go/v1", "sk-test")
    finally:
        await client.aclose()
    assert payload is not None
    assert payload["used_percent"] == 50


# --- Usage normalization (pure) ------------------------------------------


def test_normalize_keyed_windows():
    usage = load_fixture("opencode_usage.json")
    meters = normalize_usage(usage, "OpenCode Go")
    assert len(meters) == 3
    by_id = {m.id: m for m in meters}
    assert set(by_id) == {"opencode-go-rolling", "opencode-go-weekly", "opencode-go-monthly"}

    rolling = by_id["opencode-go-rolling"]
    assert rolling.provider == "opencode"
    assert rolling.label == "Rolling Usage"
    assert rolling.used_percent == 2
    assert rolling.remaining_percent == 98
    assert rolling.status == "ok"
    assert rolling.reset_at == datetime(2026, 6, 27, 22, 58, tzinfo=timezone.utc)

    monthly = by_id["opencode-go-monthly"]
    assert monthly.used_percent == 47
    assert monthly.remaining_percent == 53
    assert monthly.reset_at == datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)


def test_normalize_flat_usage():
    usage = load_fixture("opencode_usage_flat.json")
    meters = normalize_usage(usage, "OpenCode Go")
    assert len(meters) == 1
    m = meters[0]
    assert m.id == "opencode-go-monthly"
    assert m.used_percent == 35
    assert m.remaining_percent == 65
    assert m.metrics.cost_used == 7.0
    assert m.metrics.cost_limit == 20.0
    assert m.metrics.currency == "USD"


def test_normalize_nested_data_wrapper():
    usage = {"data": {"rolling": {"used_percent": 5}, "weekly": {"used_percent": 20}}}
    meters = normalize_usage(usage, "OpenCode Go")
    assert len(meters) == 2
    assert {m.id for m in meters} == {"opencode-go-rolling", "opencode-go-weekly"}


def test_normalize_empty_payload_returns_no_meters():
    assert normalize_usage({}, "OpenCode Go") == []


def test_normalize_window_with_used_and_limit_counts():
    usage = {"monthly": {"used": 15, "limit": 60}}
    meters = normalize_usage(usage, "OpenCode Go")
    assert len(meters) == 1
    assert meters[0].used_percent == 25  # 15/60
    assert meters[0].remaining_percent == 75


# --- Adapter-level 'api' mode (mocked HTTP + mocked fetcher) --------------


def _write_auth_file(tmp_path: Path, key: str = "sk-test-key") -> Path:
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps({"opencode-go": {"type": "api", "key": key}}),
        encoding="utf-8",
    )
    return path


@pytest.mark.asyncio
async def test_api_mode_full_flow_validates_and_normalizes(db_path: Path, tmp_path: Path, monkeypatch):
    from app.providers import opencode
    from app.providers.opencode import OpenCodeAdapter
    from tests.conftest import make_settings

    auth_path = _write_auth_file(tmp_path)
    models = load_fixture("opencode_models.json")
    usage = load_fixture("opencode_usage.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=models)
        if request.url.path.endswith("/usage"):
            return httpx.Response(200, json=usage)
        return httpx.Response(404)

    settings = make_settings(
        db_path,
        opencode_enabled=True,
        opencode_mode="api",
        opencode_go_auth_file=auth_path,
        opencode_api_base_url="https://opencode.ai/zen/go/v1",
    )
    adapter = OpenCodeAdapter(settings)
    adapter._client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        meters = await adapter.fetch_meters()
    finally:
        await adapter.aclose()

    assert len(meters) == 3
    assert {m.id for m in meters} == {"opencode-go-rolling", "opencode-go-weekly", "opencode-go-monthly"}


@pytest.mark.asyncio
async def test_api_mode_auth_validation_failure(db_path: Path, tmp_path: Path):
    from app.providers.opencode import OpenCodeAdapter
    from tests.conftest import make_settings

    auth_path = _write_auth_file(tmp_path, key="bad-key")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(401, json={"error": "invalid_api_key"})
        return httpx.Response(404)

    settings = make_settings(
        db_path,
        opencode_enabled=True,
        opencode_mode="api",
        opencode_go_auth_file=auth_path,
        opencode_api_base_url="https://opencode.ai/zen/go/v1",
    )
    adapter = OpenCodeAdapter(settings)
    adapter._client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        meters = await adapter.fetch_meters()
    finally:
        await adapter.aclose()

    assert len(meters) == 1
    assert meters[0].status == "error"
    assert "validation failed" in meters[0].reset_label


@pytest.mark.asyncio
async def test_api_mode_missing_auth_file(db_path: Path):
    from app.providers.opencode import OpenCodeAdapter
    from tests.conftest import make_settings

    settings = make_settings(
        db_path,
        opencode_enabled=True,
        opencode_mode="api",
        opencode_go_auth_file=Path("/nonexistent/auth.json"),
    )
    adapter = OpenCodeAdapter(settings)
    meters = await adapter.fetch_meters()
    assert len(meters) == 1
    assert meters[0].status == "error"
    assert "not found" in meters[0].reset_label


@pytest.mark.asyncio
async def test_api_mode_error_when_no_usage_endpoint(db_path: Path, tmp_path: Path):
    from app.providers.opencode import OpenCodeAdapter
    from tests.conftest import make_settings

    auth_path = _write_auth_file(tmp_path)
    models = load_fixture("opencode_models.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=models)
        return httpx.Response(404)

    settings = make_settings(
        db_path,
        opencode_enabled=True,
        opencode_mode="api",
        opencode_go_auth_file=auth_path,
        opencode_api_base_url="https://opencode.ai/zen/go/v1",
    )
    adapter = OpenCodeAdapter(settings)
    adapter._client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        meters = await adapter.fetch_meters()
    finally:
        await adapter.aclose()

    assert len(meters) == 1
    assert meters[0].status == "error"
    assert "no OpenCode Go usage endpoint" in meters[0].reset_label


@pytest.mark.asyncio
async def test_api_mode_error_when_usage_data_unparseable(db_path: Path, tmp_path: Path):
    from app.providers.opencode import OpenCodeAdapter
    from tests.conftest import make_settings

    auth_path = _write_auth_file(tmp_path)
    models = load_fixture("opencode_models.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=models)
        if request.url.path.endswith("/usage"):
            return httpx.Response(200, json={"unrelated": "data"})
        return httpx.Response(404)

    settings = make_settings(
        db_path,
        opencode_enabled=True,
        opencode_mode="api",
        opencode_go_auth_file=auth_path,
        opencode_api_base_url="https://opencode.ai/zen/go/v1",
    )
    adapter = OpenCodeAdapter(settings)
    adapter._client = httpx.AsyncClient(transport=_mock_transport(handler))
    try:
        meters = await adapter.fetch_meters()
    finally:
        await adapter.aclose()

    assert len(meters) == 1
    assert meters[0].status == "error"
    assert "parseable usage data" in meters[0].reset_label
