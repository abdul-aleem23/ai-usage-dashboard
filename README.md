# AI Usage Dashboard

AI Usage Dashboard is a self-hosted usage monitor for AI services. It has two separate parts:

- **Backend**: a FastAPI service that collects provider usage and exposes normalized JSON.
- **ESP32 dashboard**: firmware for a Cheap Yellow Display (CYD) that renders provider-specific usage bars.

The backend is intended to run on a small server behind Cloudflare Tunnel. The ESP32 display calls the compact API endpoint and shows remaining usage at a glance.

## Supported Providers

| Provider | Current Signal | Notes |
| --- | --- | --- |
| Codex / OpenAI subscription | 5-hour and weekly usage windows | Uses ChatGPT/Codex subscription usage endpoints. |
| GitHub Copilot | Chat and completions quota | Uses GitHub Copilot account quota data. |
| DeepSeek | Wallet balance | Rendered against a configurable balance target. |
| OpenCode Go/Zen | Optional / disabled by default | Code exists, but reliable server auth is not enabled by default. |

## Repository Layout

```text
backend/      FastAPI backend service
dashboard/    ESP32 CYD dashboard documentation and firmware
docs/         API, architecture, and deployment documentation
secrets/      Local/server secrets, ignored by git
```

## API Summary

The ESP32 dashboard should use:

```text
GET /api/v1/summary.compact
X-API-Key: <ESP32_API_KEY>
```

The compact payload is optimized for microcontrollers and contains one meter per provider quota. Providers are displayed separately; usage is not combined across platforms.

## Documentation

- [Backend](backend/README.md)
- [ESP32 Dashboard](dashboard/README.md)
- [API](docs/API.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Deployment](docs/DEPLOYMENT.md)