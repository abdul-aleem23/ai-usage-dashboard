"""Application settings.

Secrets are sourced from environment variables or mounted files in V1 and are
never committed to the database. Multiple Codex accounts are configured via a
comma-separated ``CODEX_ACCOUNTS`` list and per-account ``CODEX_<LABEL>_AUTH_FILE``
paths.
"""

from __future__ import annotations

import re
import os
import json
from pathlib import Path
from typing import Any, Optional

from fastapi import Request
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import dotenv_values


def _slug(label: str) -> str:
    """Normalize an account label into an env-var-friendly slug."""
    return re.sub(r"[^A-Z0-9]", "_", label.strip().upper())


class CodexAccountConfig(BaseModel):
    """A single configured Codex account."""

    label: str
    slug: str
    auth_file: Path

    @property
    def account_id(self) -> str:
        return f"codex-{self.slug.lower()}"


class Settings(BaseSettings):
    """Runtime configuration loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Core ---------------------------------------------------------------
    admin_api_key: str = Field(..., description="Admin key for write/refresh ops")
    esp32_api_key: str = Field(..., description="Read-only key for ESP32 clients")
    db_path: Path = Field(Path("data/aiusage.db"), description="SQLite path")
    refresh_interval_minutes: int = 15
    refresh_on_startup: bool = True
    request_timeout_seconds: float = 20.0
    user_agent: str = "ai-usage-backend/1.0"
    log_level: str = "INFO"

    # --- Codex / OpenAI subscription ---------------------------------------
    codex_accounts: str = Field("", description="Comma-separated account labels")
    codex_accounts_file: Optional[Path] = Field(None, description="JSON registry for admin-added Codex accounts")
    codex_auth_upload_dir: Path = Field(Path("/secrets"), description="Directory for admin-added Codex auth JSON files")
    codex_oauth_token_url: str = "https://auth.openai.com/oauth/token"
    codex_usage_url: str = "https://chatgpt.com/backend-api/wham/usage"

    # --- GitHub Copilot -----------------------------------------------------
    copilot_token: Optional[str] = None
    copilot_token_file: Optional[Path] = None
    copilot_api_url: str = "https://api.github.com/copilot_internal/user"

    # --- DeepSeek -----------------------------------------------------------
    deepseek_api_key: Optional[str] = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_balance_target_usd: float = 5.0
    deepseek_low_balance_usd: float = 1.0

    # --- OpenCode Go/Zen ----------------------------------------------------
    # Master switch for the provider.
    opencode_enabled: bool = False
    opencode_label: str = "OpenCode Go"
    # Collection strategy: "static" reads configured limits from env; "api"
    # uses an OpenCode Go API key to validate auth and probe usage endpoints.
    opencode_mode: str = "static"
    # Static-mode configuration.
    opencode_monthly_limit_usd: float = 20.0
    opencode_monthly_used_usd: float = 0.0
    opencode_reset_day_of_month: int = 1
    # API-mode configuration.
    opencode_api_base_url: str = "https://opencode.ai/zen/go/v1"
    opencode_go_auth_file: Optional[Path] = None

    # --- Auth helpers -------------------------------------------------------
    @field_validator("admin_api_key", "esp32_api_key")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("API key must be set")
        return v.strip()

    @field_validator("opencode_mode")
    @classmethod
    def _opencode_mode(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("static", "api"):
            raise ValueError("opencode_mode must be 'static' or 'api'")
        return v

    def codex_account_configs(self) -> list[CodexAccountConfig]:
        """Resolve configured and admin-added Codex account auth files."""
        configs: list[CodexAccountConfig] = []
        seen: set[str] = set()

        for raw in self.codex_accounts.split(","):
            label = raw.strip()
            if not label:
                continue
            slug = _slug(label)
            env_name = f"CODEX_{slug}_AUTH_FILE"
            path_value = _env_lookup(env_name)
            if not path_value:
                raise ValueError(
                    f"Codex account '{label}' listed but {env_name} is not set"
                )
            configs.append(
                CodexAccountConfig(
                    label=label,
                    slug=slug.lower(),
                    auth_file=_resolve_config_path(path_value),
                )
            )
            seen.add(slug.lower())

        for entry in _load_codex_accounts_file(self.codex_accounts_file):
            label = str(entry.get("label") or "").strip()
            auth_file = str(entry.get("auth_file") or "").strip()
            if not label or not auth_file:
                continue
            slug = _slug(label).lower()
            if slug in seen:
                continue
            configs.append(
                CodexAccountConfig(
                    label=label,
                    slug=slug,
                    auth_file=_resolve_config_path(auth_file),
                )
            )
            seen.add(slug)

        return configs

    def copilot_token_value(self) -> Optional[str]:
        if self.copilot_token:
            return self.copilot_token.strip()
        if self.copilot_token_file:
            token_path = _resolve_config_path(str(self.copilot_token_file))
            return token_path.read_text(encoding="utf-8").strip()
        return None


def _env_lookup(name: str) -> Optional[str]:
    """Look up dynamic env vars from real env or the same .env sources.

    Pydantic loads declared settings from ``.env`` but dynamic names like
    ``CODEX_ACCOUNT1_AUTH_FILE`` are not declared fields. Reading only
    ``os.environ`` misses local .env values, so check both process env and the
    project/backend .env files.
    """
    value = os.environ.get(name)
    if value:
        return value
    for env_file in _candidate_env_files():
        if not env_file.exists():
            continue
        raw = dotenv_values(env_file).get(name)
        if raw:
            return str(raw)
    return None


def _candidate_env_files() -> list[Path]:
    backend_dir = Path(__file__).resolve().parents[1]
    return [Path.cwd() / ".env", backend_dir / ".env", backend_dir.parent / ".env"]


def _resolve_config_path(value: str) -> Path:
    """Resolve secret/data paths robustly for local and Docker runs.

    Docker uses absolute paths like ``/secrets/...``. Local Windows runs often
    use relative paths and users may place ``secrets/`` either at project root or
    inside ``backend/``. Try the literal path first, then sensible project paths.
    """
    raw = value.strip()
    path = Path(raw)
    if path.is_absolute() or path.exists():
        return path

    backend_dir = Path(__file__).resolve().parents[1]
    candidates = [
        Path.cwd() / path,
        backend_dir / path,
        backend_dir.parent / path,
    ]

    # Be forgiving when a local run uses Docker-style/root-style relative paths
    # such as ../secrets/foo but the file actually lives in backend/secrets/foo.
    parts = path.parts
    if parts and parts[0] == ".." and len(parts) > 1:
        without_parent = Path(*parts[1:])
        candidates.extend([backend_dir / without_parent, backend_dir.parent / without_parent])

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return path


def get_settings(request: Request) -> Settings:
    """FastAPI dependency returning the app's cached settings.

    Reuses the instance stored on ``app.state.settings`` by ``create_app``;
    falls back to constructing from env if it is missing.
    """
    settings = getattr(request.app.state, "settings", None)
    if settings is not None:
        return settings
    return Settings()  # type: ignore[call-arg]



def _load_codex_accounts_file(path: Optional[Path]) -> list[dict[str, Any]]:
    """Load admin-managed Codex account registry entries."""
    registry = _codex_accounts_registry_path(path)
    if not registry.exists():
        return []
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    accounts = data.get("accounts") if isinstance(data, dict) else None
    return accounts if isinstance(accounts, list) else []


def _codex_accounts_registry_path(path: Optional[Path]) -> Path:
    if path is not None:
        return _resolve_config_path(str(path))
    default = Path("/secrets/codex-accounts.json")
    if default.parent.exists():
        return default
    backend_dir = Path(__file__).resolve().parents[1]
    return backend_dir.parent / "secrets" / "codex-accounts.json"
