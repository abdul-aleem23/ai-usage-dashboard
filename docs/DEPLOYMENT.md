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

By default the backend container publishes port `8000` on the host. If your existing `cloudflared` tunnel runs as a host systemd service, route it to localhost:

```text
http://127.0.0.1:8000
```

If your existing `cloudflared` tunnel runs in Docker, `127.0.0.1` points inside the `cloudflared` container. In that case route it to the Docker bridge host address instead, usually:

```text
http://172.17.0.1:8000
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
   - URL for host-installed `cloudflared`: `127.0.0.1:8000`
   - URL for Docker-based `cloudflared`: `172.17.0.1:8000`
5. Save and wait for the route to become active.

To confirm the Docker bridge address on the server:

```bash
ip addr show docker0 | grep "inet "
```

To test from a Docker-based tunnel, use the bridge address in the public hostname route and then restart the tunnel container:

```bash
docker restart cloudflared
curl https://ai-usage.example.com/health
```

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

OpenCode collection is optional. The code supports static and API modes only. Browser-based scraping is intentionally not included.
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

### Simple server deploy script

This repository includes `scripts/deploy-server.sh` for manual server deployments. It pulls the latest `main`, rebuilds/restarts the backend container, and checks the local health endpoint.

First-time setup on the server:

```bash
cd ~/monday/projects/personal/ai-usage-dashboard
chmod +x scripts/deploy-server.sh
```

Deploy after a successful GitHub Actions run:

```bash
./scripts/deploy-server.sh
```

Override the project location only if your checkout lives somewhere else:

```bash
APP_DIR=/opt/ai-usage ./scripts/deploy-server.sh
```

## 8. CI/CD

GitHub Actions runs on every push and pull request to `main`:

- installs backend dependencies in a clean Python 3.12 environment
- runs the backend test suite
- builds the Docker image

This is CI: it proves the code is healthy before you deploy it.

Deployment is intentionally manual for now: log into the home server and run `./scripts/deploy-server.sh`. This keeps secrets on the server, avoids giving GitHub SSH access to the home server, and is easier to audit while the project is still early.

## 9. Optional hardening

- Put Cloudflare Access (Zero Trust) in front of `/docs` and admin endpoints.
- Rotate API keys by updating `backend/.env` and `docker compose up -d`.
- Move SQLite to a dedicated volume or migrate to Postgres for V1.1+.

## 10. Server Information Needed

When configuring a real server, collect these values first:

- Public hostname, for example `ai-usage.example.com`.
- Whether your existing tunnel is dashboard-managed or `config.yml`-managed.
- If using `config.yml`, the path to that file on the server.
- The local service URL, normally `http://127.0.0.1:8000`.
- The final `ESP32_API_KEY` value that firmware will use.