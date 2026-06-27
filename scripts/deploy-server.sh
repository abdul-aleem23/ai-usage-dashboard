#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/monday/projects/personal/ai-usage-dashboard}"
SERVICE_URL="${SERVICE_URL:-http://127.0.0.1:8000/health}"

cd "$APP_DIR"

echo "==> Pulling latest code"
git pull --ff-only

echo "==> Rebuilding and restarting backend"
docker compose up -d --build ai-usage

echo "==> Waiting for health endpoint"
for attempt in {1..30}; do
  if curl -fsS "$SERVICE_URL" >/dev/null; then
    echo "==> Backend healthy: $SERVICE_URL"
    docker compose logs --tail=40 ai-usage
    exit 0
  fi
  sleep 2
done

echo "ERROR: backend did not become healthy at $SERVICE_URL" >&2
docker compose logs --tail=120 ai-usage >&2
exit 1
