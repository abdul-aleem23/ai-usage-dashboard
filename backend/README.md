# Backend

The backend is a FastAPI service that collects AI usage data from configured providers, normalizes it into a common meter shape, stores current and historical values in SQLite, and serves authenticated API endpoints for dashboards.

## Responsibilities

- Refresh provider usage on a schedule.
- Isolate provider failures so one failed provider does not break the whole refresh cycle.
- Store latest meter values and historical snapshots.
- Expose full and compact summary endpoints.
- Keep secrets out of the database.

## Local Development

```bash
cd backend
python -m venv .venv
. .venv/Scripts/activate        # Windows
# source .venv/bin/activate     # Linux/macOS
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Run tests:

```bash
pytest -q
```

## Configuration

Configuration is read from environment variables or `backend/.env`.

Required:

- `ADMIN_API_KEY`
- `ESP32_API_KEY`
- `DB_PATH`

Provider configuration is optional. Enable only the providers you intend to display.

### Codex / OpenAI Subscription

One or more Codex accounts can be configured. Account labels are not hardcoded; choose any display strings that make sense for your setup.

```env
CODEX_ACCOUNTS=main,team-a
CODEX_MAIN_AUTH_FILE=/secrets/codex-main-auth.json
CODEX_TEAM_A_AUTH_FILE=/secrets/codex-team-a-auth.json
```

Each label becomes a separate account. Labels are normalized only for environment variable names and stable ids. For example, `team-a` uses `CODEX_TEAM_A_AUTH_FILE`, becomes `codex-team_a`, and compact meters include `"al":"team-a"`.

### GitHub Copilot

```env
COPILOT_TOKEN_FILE=/secrets/copilot-token.txt
```

### DeepSeek

```env
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_BALANCE_TARGET_USD=5.00
DEEPSEEK_LOW_BALANCE_USD=1.00
```

### OpenCode

OpenCode support is disabled by default:

```env
OPENCODE_ENABLED=false
```

## Endpoints

See [API](../docs/API.md).
## Codex Account Manager

Codex auth tokens are account-scoped. If you log into a different Codex/OpenAI account, the previous account's refresh token can stop working. The backend therefore supports adding or replacing Codex auth JSON files through an admin page:

```text
https://ai-usage.forexstreet-bmm.com/admin/codex
```

Flow:

1. Log into the desired Codex account on a trusted machine.
2. Copy the Codex auth JSON for that account.
3. Open `/admin/codex` on this service.
4. Enter the admin API key, a label such as `main` or `backup`, and the auth JSON.
5. Save and refresh.

The account label is user-defined. It is shown in API responses and on the ESP32 dashboard. The auth JSON is stored in the server `secrets/` mount, and the registry is stored as `codex-accounts.json` in the same directory.

This page does not perform browser login. It only stores auth JSON that you provide.

