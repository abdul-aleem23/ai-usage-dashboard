# Ubuntu Home Server Deployment (Docker Compose + Cloudflare Tunnel)

This guide deploys the backend on an Ubuntu home server behind a Cloudflare Tunnel so
the ESP32 and other clients reach it over HTTPS without opening any inbound
ports.

## 1. Prerequisites

- Ubuntu 22.04+ home server with Docker and the Compose plugin.
- A Cloudflare account with a domain managed by Cloudflare.
- Your provider secrets (Codex auth JSON, GitHub Copilot token, DeepSeek key).

## 2. Lay out the project on the host

```bash
git clone <your-repo> /opt/ai-usage
cd /opt/ai-usage
```

Create the secrets directory (kept out of git):

```bash
mkdir -p secrets
# Codex auth files (one per account listed in CODEX_ACCOUNTS)
cp ~/codex-main-auth.json secrets/codex-main-auth.json
# GitHub Copilot token
echo -n "ghp_yourtoken" > secrets/copilot-token.txt
chmod 600 secrets/*
```

Configure the app:

```bash
cp backend/.env.example backend/.env
# Edit backend/.env:
#   ADMIN_API_KEY, ESP32_API_KEY  -> strong random values
#   CODEX_ACCOUNTS + CODEX_<LABEL>_AUTH_FILE paths, one per Codex account label
#   COPILOT_TOKEN_FILE=/secrets/copilot-token.txt
#   DEEPSEEK_API_KEY
```

Generate strong keys:

```bash
openssl rand -hex 32   # use one for ADMIN_API_KEY
openssl rand -hex 32   # use one for ESP32_API_KEY
```

## 3. Add a Cloudflare Tunnel route

If your home server already runs `cloudflared` for other services, use the existing tunnel. You only need to add one hostname route for this backend.

Recommended hostname:

```text
ai-usage.example.com
```

The backend container binds to localhost only:

```text
http://127.0.0.1:8000
```

### Option A: existing cloudflared config file

If your tunnel uses a local `config.yml`, add this ingress rule above the final `http_status:404` rule:

```yaml
ingress:
  - hostname: ai-usage.example.com
    service: http://127.0.0.1:8000
  - service: http_status:404
```

Then restart your existing `cloudflared` service:

```bash
sudo systemctl restart cloudflared
sudo systemctl status cloudflared --no-pager
```

### Option B: Cloudflare Zero Trust dashboard

If your tunnel is configured in the Cloudflare dashboard:

1. Open Cloudflare Zero Trust.
2. Go to **Networks -> Tunnels**.
3. Select your existing home-server tunnel.
4. Add a **Public Hostname**:
   - Subdomain: `ai-usage`
   - Domain: your domain
   - Type: `HTTP`
   - URL: `127.0.0.1:8000`
5. Save and wait for the route to become active.

### Option C: project-managed tunnel container

This repo also includes an optional `cloudflared` service for users who want this project to manage its own tunnel. It is disabled by default and only starts with the `managed-tunnel` profile.

Create `cloudflare/.env`:

```env
TUNNEL_TOKEN=<cloudflare tunnel token>
```

Then start with:

```bash
docker compose --profile managed-tunnel up -d --build
```

For your server, prefer Option A or B because you already run other tunnel routes.
## 4. Start the stack

```bash
docker compose up -d --build
docker compose logs -f ai-usage
```

Smoke test from the host:

```bash
curl http://127.0.0.1:8000/health
curl -H "X-API-Key: $ESP32_API_KEY" http://127.0.0.1:8000/api/v1/summary
```

Then over the public hostname:

```bash
curl https://ai-usage.example.com/health
curl -H "X-API-Key: $ESP32_API_KEY" https://ai-usage.example.com/api/v1/summary.compact
```

## 5. Verify provider readiness

OpenCode is optional and disabled by default:

```env
OPENCODE_ENABLED=false
OPENCODE_MODE=static
```

Then verify the active providers from the host:

```bash
curl -H "X-API-Key: $ADMIN_API_KEY" -X POST http://127.0.0.1:8000/api/v1/admin/refresh
curl -H "X-API-Key: $ESP32_API_KEY" http://127.0.0.1:8000/api/v1/providers
curl -H "X-API-Key: $ESP32_API_KEY" http://127.0.0.1:8000/api/v1/summary.compact
```

Expected active providers:

- Codex / OpenAI subscription
- GitHub Copilot
- DeepSeek

Each configured provider should report `ok` or a meaningful account-level error. A provider error should not crash refresh for the other providers.

### OpenCode status

OpenCode collection is optional. The code supports static, API, and Playwright modes, but server-side browser authentication may require a reliable cookie export or a provider usage API.
## 6. ESP32 client usage

```cpp
// HTTPClient or WiFiClientSecure with root CAs; call:
// GET https://ai-usage.example.com/api/v1/summary.compact
// header: X-API-Key: <ESP32_API_KEY>
```

The compact payload is small enough to parse with ArduinoJson on devices with
limited heap. Refresh on the device every few minutes; the backend refreshes
its cache every `REFRESH_INTERVAL_MINUTES` regardless.

## 7. Operations

- **Trigger a manual refresh:** `POST /api/v1/admin/refresh` with the admin key.
- **View provider status:** `GET /api/v1/providers`.
- **Backups:** stateful data is `backend/data/aiusage.db` (usage history). Back up `secrets/` separately and keep it private.
- **Updating:** `git pull && docker compose up -d --build`.
- **Logs:** `docker compose logs -f`.

## 8. Optional hardening

- Put Cloudflare Access (Zero Trust) in front of `/docs` and admin endpoints.
- Rotate API keys by updating `backend/.env` and `docker compose up -d`.
- Move SQLite to a dedicated volume or migrate to Postgres for V1.1+.

## 9. Server Information Needed

When configuring a real server, collect these values first:

- Public hostname, for example `ai-usage.example.com`.
- Whether your existing tunnel is dashboard-managed or `config.yml`-managed.
- If using `config.yml`, the path to that file on the server.
- The local service URL, normally `http://127.0.0.1:8000`.
- The final `ESP32_API_KEY` value that firmware will use.