# Architecture

## Overview

```text
ESP32 dashboard / other clients
        |
        | HTTPS + API key
        v
Cloudflare Tunnel
        |
        v
FastAPI backend
        |
        |-- Provider adapters: Codex, GitHub Copilot, DeepSeek
        |-- Scheduler: periodic refresh
        |-- SQLite: latest meters and history
```

The backend is the only component that talks to upstream provider APIs. Clients only read normalized data from the backend.

## Backend Flow

1. Provider adapters fetch usage data from upstream services.
2. Data is normalized into `UsageMeter` objects.
3. Latest values are written to `usage_meters`.
4. Historical snapshots are appended to `usage_snapshots`.
5. Read endpoints serve full or compact dashboard payloads.

Provider failures are isolated. A failed provider can produce an error meter, but it should not stop other providers from refreshing.

## Dashboard Flow

1. ESP32 connects to Wi-Fi.
2. ESP32 calls `/api/v1/summary.compact` with `ESP32_API_KEY`.
3. Firmware parses the compact JSON payload.
4. Each provider quota is rendered as a separate horizontal remaining-usage bar.
5. The display keeps the last successful payload if a refresh fails.

## Data Model

- `provider_accounts`: configured provider accounts and labels.
- `usage_meters`: latest normalized meter values.
- `usage_snapshots`: append-only meter history.
- `sync_runs`: refresh runs, status, and errors.

Secrets are referenced by path or environment variable name and are never stored in SQLite.

## Auth Model

- `ESP32_API_KEY`: read-only key for dashboard clients.
- `ADMIN_API_KEY`: admin key for manual refresh and all read endpoints.

Keys can be sent with `X-API-Key` or `Authorization: Bearer <key>`.

## Compact Payload Discipline

The ESP32 should use `/api/v1/summary.compact`. The compact payload uses short keys to reduce response size and JSON parsing memory.

The full `/api/v1/summary` endpoint is available for richer clients and debugging.

## Provider Notes

- Codex/OpenAI and GitHub Copilot use private or undocumented endpoints, so parsers are defensive.
- DeepSeek uses the official balance endpoint.
- OpenCode support is optional and disabled by default.