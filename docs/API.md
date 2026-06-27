# API

All `/api/v1/*` endpoints require an API key. Send it with either:

```text
X-API-Key: <key>
```

or:

```text
Authorization: Bearer <key>
```

## Endpoints

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/health` | none | Liveness check. |
| `GET` | `/api/v1/summary` | read/admin | Full dashboard payload. |
| `GET` | `/api/v1/summary.compact` | read/admin | Compact ESP32 payload. |
| `GET` | `/api/v1/providers` | read/admin | Provider/account status list. |
| `GET` | `/api/v1/providers/{id}` | read/admin | Provider/account detail. |
| `POST` | `/api/v1/admin/refresh` | admin | Trigger a refresh immediately. |

## Full Meter Shape

```json
{
  "id": "codex-main-5h",
  "provider": "codex",
  "account_id": "codex-main",
  "account_label": "main",
  "label": "5 hour usage limit",
  "used_percent": 31,
  "remaining_percent": 69,
  "reset_at": "2026-06-27T22:58:00+00:00",
  "reset_label": "Resets in 4h 21m",
  "status": "ok",
  "updated_at": "2026-06-27T18:46:00+00:00",
  "metrics": {
    "tokens_used": 31,
    "tokens_limit": 100,
    "unit": "tokens"
  }
}
```

## Compact Summary Shape

```json
{
  "ts": "2026-06-27T18:46:00+00:00",
  "m": [
    {
      "id": "codex-main-5h",
      "p": "codex",
      "al": "main",
      "l": "5 hour usage limit",
      "u": 31,
      "r": 69,
      "s": "ok",
      "rt": "2026-06-27T22:58:00+00:00"
    }
  ],
  "a": []
}
```

Compact meter keys:

| Key | Meaning |
| --- | --- |
| `id` | Stable meter id. |
| `p` | Provider id. |
| `al` | Account label, when available. |
| `l` | Display label. |
| `u` | Used percent. |
| `r` | Remaining percent. |
| `s` | Status. |
| `rt` | Reset time, if available. |

## Status Values

| Status | Meaning |
| --- | --- |
| `ok` | Healthy remaining usage. |
| `warning` | Low remaining usage. |
| `critical` | Nearly exhausted. |
| `unknown` | Percentage is not available. |
| `error` | Provider refresh failed. |