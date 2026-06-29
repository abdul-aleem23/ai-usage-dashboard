"""Admin endpoints and small admin UI."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..auth import require_admin_key
from ..config import Settings, _codex_accounts_registry_path, _slug, get_settings
from ..models import DashboardSummary
from ..refresh import refresh_all

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
ui_router = APIRouter(tags=["admin-ui"])

_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,47}$")


class CodexAccountUpload(BaseModel):
    label: str = Field(..., min_length=1, max_length=48)
    auth_json: dict[str, Any]
    refresh: bool = True


@router.post("/refresh", response_model=DashboardSummary)
async def trigger_refresh(
    _key: str = Depends(require_admin_key),
    settings: Settings = Depends(get_settings),
) -> DashboardSummary:
    return await refresh_all(settings)


@router.get("/codex/accounts")
def list_codex_accounts(
    _key: str = Depends(require_admin_key),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return {
        "accounts": [
            {
                "label": account.label,
                "account_id": account.account_id,
                "auth_file": str(account.auth_file),
                "exists": account.auth_file.exists(),
            }
            for account in settings.codex_account_configs()
        ]
    }


@router.post("/codex/accounts")
async def upsert_codex_account(
    payload: CodexAccountUpload,
    _key: str = Depends(require_admin_key),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    label = payload.label.strip()
    if not _LABEL_PATTERN.fullmatch(label):
        raise HTTPException(
            status_code=400,
            detail="Label must start with a letter/number and use only letters, numbers, spaces, _, -, or .",
        )
    _validate_codex_auth(payload.auth_json)

    upload_dir = _codex_upload_dir(settings)
    upload_dir.mkdir(parents=True, exist_ok=True)

    slug = _slug(label).lower()
    auth_file = upload_dir / f"codex-{slug}-auth.json"
    auth_file.write_text(json.dumps(payload.auth_json, indent=2), encoding="utf-8")

    registry_path = _codex_accounts_registry_path(settings.codex_accounts_file)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry = _load_registry(registry_path)
    accounts = [a for a in registry.get("accounts", []) if _slug(str(a.get("label", ""))).lower() != slug]
    accounts.append({"label": label, "auth_file": str(auth_file)})
    registry["accounts"] = sorted(accounts, key=lambda a: str(a.get("label", "")).lower())
    registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")

    summary = await refresh_all(settings) if payload.refresh else None
    return {
        "status": "ok",
        "label": label,
        "account_id": f"codex-{slug}",
        "auth_file": str(auth_file),
        "registry": str(registry_path),
        "refreshed": summary is not None,
        "summary": summary.model_dump(mode="json") if summary else None,
    }


@ui_router.get("/admin/codex", response_class=HTMLResponse)
def codex_admin_page() -> str:
    return _CODEX_ADMIN_HTML


def _validate_codex_auth(auth: dict[str, Any]) -> None:
    tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
    refresh_token = tokens.get("refresh_token") or auth.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise HTTPException(status_code=400, detail="Codex auth JSON must contain tokens.refresh_token")


def _codex_upload_dir(settings: Settings) -> Path:
    configured = settings.codex_auth_upload_dir
    if configured.exists() or str(configured) != "/secrets":
        return configured
    backend_dir = Path(__file__).resolve().parents[1]
    return backend_dir.parent / "secrets"


def _load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"accounts": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"accounts": []}
    return data if isinstance(data, dict) else {"accounts": []}


_CODEX_ADMIN_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Codex Accounts - AI Usage</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: #071012; color: #d5fff7; font-family: system-ui, -apple-system, Segoe UI, sans-serif; padding: 32px 18px; }
    main { max-width: 900px; margin: 0 auto; display: grid; gap: 18px; }
    h1, p { margin: 0; }
    h1 { font-size: 26px; }
    p { color: #86aead; line-height: 1.45; }
    .panel { background: #0b1517; border: 1px solid #1e5b62; padding: 18px; display: grid; gap: 14px; }
    label { display: grid; gap: 7px; color: #d5fff7; font-weight: 700; }
    input, textarea { width: 100%; border: 1px solid #1e5b62; background: #020405; color: #d5fff7; padding: 11px; font: 14px ui-monospace, SFMono-Regular, Consolas, monospace; }
    textarea { min-height: 260px; resize: vertical; }
    button { justify-self: start; border: 0; background: #00d9ff; color: #001114; padding: 11px 16px; font-weight: 800; cursor: pointer; }
    button:disabled { opacity: .55; cursor: wait; }
    pre { margin: 0; white-space: pre-wrap; background: #020405; border: 1px solid #1e5b62; padding: 14px; color: #b8d7d5; overflow: auto; }
    .warning { color: #ffcc33; }
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Codex Account Manager</h1>
      <p>Add or replace a Codex auth JSON under a label. The backend stores the file, refreshes usage, and the normal dashboard/ESP32 endpoints then include that account.</p>
    </header>

    <section class="panel">
      <p class="warning">Use this only over your HTTPS Cloudflare hostname. The admin key and Codex auth JSON are sensitive.</p>
      <label>Admin API key
        <input id="key" type="password" autocomplete="off" placeholder="admin_..." />
      </label>
      <label>Account label
        <input id="label" type="text" placeholder="main, backup, client-a, etc." />
      </label>
      <label>Codex auth JSON
        <textarea id="auth" spellcheck="false" placeholder='{"OPENAI_API_KEY": null, "tokens": {"refresh_token": "..."}}'></textarea>
      </label>
      <button id="save">Save account and refresh</button>
      <pre id="out">Ready.</pre>
    </section>
  </main>

  <script>
    const key = document.getElementById('key');
    const label = document.getElementById('label');
    const auth = document.getElementById('auth');
    const save = document.getElementById('save');
    const out = document.getElementById('out');

    save.addEventListener('click', async () => {
      save.disabled = true;
      out.textContent = 'Saving...';
      try {
        const authJson = JSON.parse(auth.value);
        const resp = await fetch('/api/v1/admin/codex/accounts', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-API-Key': key.value.trim(),
          },
          body: JSON.stringify({ label: label.value.trim(), auth_json: authJson, refresh: true }),
        });
        const body = await resp.json();
        if (!resp.ok) throw new Error(body.detail || `HTTP ${resp.status}`);
        out.textContent = JSON.stringify(body, null, 2);
      } catch (err) {
        out.textContent = `Error: ${err.message}`;
      } finally {
        save.disabled = false;
      }
    });
  </script>
</body>
</html>"""
