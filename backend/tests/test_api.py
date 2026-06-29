"""API endpoint tests using FastAPI TestClient with a seeded SQLite DB."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import MeterMetrics, UsageMeter
from app.repository import upsert_account, write_meters


def _seed(settings) -> None:
    upsert_account(
        settings.db_path,
        account_id="codex-personal",
        provider="codex",
        label="Personal",
        auth_type="oauth",
        secret_ref="/secrets/codex-personal-auth.json",
    )
    write_meters(
        settings.db_path,
        [
            UsageMeter(
                id="codex-personal-5h",
                provider="codex",
                account_id="codex-personal",
                account_label="Personal",
                label="5 hour usage limit",
                used_percent=90,
                remaining_percent=10,
                reset_at=datetime(2026, 6, 27, 22, 58, tzinfo=timezone.utc),
                reset_label="Resets in 4h 12m",
                status="warning",
                updated_at=datetime(2026, 6, 27, 18, 46, tzinfo=timezone.utc),
                metrics=MeterMetrics(tokens_used=90, tokens_limit=100, unit="tokens"),
            )
        ],
    )


def _client(settings):
    app = create_app(settings)
    return TestClient(app)


def test_health_no_auth(settings):
    with _client(settings) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_summary_requires_key(settings):
    with _client(settings) as client:
        resp = client.get("/api/v1/summary")
        assert resp.status_code == 401


def test_summary_with_esp32_key(settings):
    _seed(settings)
    with _client(settings) as client:
        resp = client.get("/api/v1/summary", headers={"X-API-Key": "test-esp32-key"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["meters"][0]["id"] == "codex-personal-5h"
        assert body["meters"][0]["remaining_percent"] == 10
        assert any(a["level"] == "warning" for a in body["alerts"])


def test_summary_compact_short_keys(settings):
    _seed(settings)
    with _client(settings) as client:
        resp = client.get("/api/v1/summary.compact", headers={"X-API-Key": "test-esp32-key"})
        assert resp.status_code == 200
        body = resp.json()
        assert "ts" in body and "m" in body and "a" in body
        assert body["m"][0]["p"] == "codex"
        assert body["m"][0]["al"] == "Personal"
        assert "provider" not in body["m"][0]


def test_admin_key_accepted_for_read(settings):
    _seed(settings)
    with _client(settings) as client:
        resp = client.get("/api/v1/summary", headers={"X-API-Key": "test-admin-key"})
        assert resp.status_code == 200


def test_providers_list_and_detail(settings):
    _seed(settings)
    with _client(settings) as client:
        resp = client.get("/api/v1/providers", headers={"X-API-Key": "test-esp32-key"})
        assert resp.status_code == 200
        providers = resp.json()
        assert providers[0]["id"] == "codex-personal"

        detail = client.get(
            "/api/v1/providers/codex", headers={"X-API-Key": "test-esp32-key"}
        )
        assert detail.status_code == 200

        missing = client.get(
            "/api/v1/providers/nope", headers={"X-API-Key": "test-esp32-key"}
        )
        assert missing.status_code == 404


def test_admin_refresh_requires_admin_key(settings):
    with _client(settings) as client:
        assert client.post("/api/v1/admin/refresh").status_code == 403
        assert (
            client.post(
                "/api/v1/admin/refresh", headers={"X-API-Key": "test-esp32-key"}
            ).status_code
            == 403
        )


def test_admin_refresh_with_admin_key(settings):
    _seed(settings)
    with _client(settings) as client:
        resp = client.post("/api/v1/admin/refresh", headers={"X-API-Key": "test-admin-key"})
        assert resp.status_code == 200
        body = resp.json()
        # No live providers configured -> stale latest meters are cleared.
        assert body["meters"] == []


def test_admin_codex_page_available(settings):
    with _client(settings) as client:
        resp = client.get("/admin/codex")
        assert resp.status_code == 200
        assert "Codex Account Manager" in resp.text


def test_admin_codex_upload_requires_admin_key(settings, tmp_path):
    settings.codex_auth_upload_dir = tmp_path
    settings.codex_accounts_file = tmp_path / "codex-accounts.json"
    payload = {
        "label": "backup",
        "auth_json": {"tokens": {"refresh_token": "rt-test"}},
        "refresh": False,
    }
    with _client(settings) as client:
        resp = client.post("/api/v1/admin/codex/accounts", json=payload)
        assert resp.status_code == 403


def test_admin_codex_upload_persists_registry(settings, tmp_path):
    settings.codex_auth_upload_dir = tmp_path
    settings.codex_accounts_file = tmp_path / "codex-accounts.json"
    payload = {
        "label": "backup",
        "auth_json": {"tokens": {"refresh_token": "rt-test", "access_token": "at-test"}},
        "refresh": False,
    }
    with _client(settings) as client:
        resp = client.post(
            "/api/v1/admin/codex/accounts",
            headers={"X-API-Key": "test-admin-key"},
            json=payload,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["account_id"] == "codex-backup"
        assert (tmp_path / "codex-backup-auth.json").exists()
        assert (tmp_path / "codex-accounts.json").exists()

        listed = client.get(
            "/api/v1/admin/codex/accounts",
            headers={"X-API-Key": "test-admin-key"},
        )
        assert listed.status_code == 200
        assert listed.json()["accounts"][0]["label"] == "backup"


def test_admin_codex_upload_rejects_missing_refresh_token(settings, tmp_path):
    settings.codex_auth_upload_dir = tmp_path
    settings.codex_accounts_file = tmp_path / "codex-accounts.json"
    with _client(settings) as client:
        resp = client.post(
            "/api/v1/admin/codex/accounts",
            headers={"X-API-Key": "test-admin-key"},
            json={"label": "backup", "auth_json": {"tokens": {}}, "refresh": False},
        )
        assert resp.status_code == 400
