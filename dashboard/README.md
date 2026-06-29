# ESP32 Dashboard

The dashboard is designed for the ESP32 Cheap Yellow Display (CYD), commonly sold as `ESP32-2432S028R`.

## Hardware

- ESP32 with Wi-Fi
- 2.8 inch 320x240 TFT display
- ILI9341-compatible display driver configuration
- Resistive touch controller available, but not required for the first interface
- Recommended display library: `TFT_eSPI`
- Recommended JSON library: `ArduinoJson`

Reference board repository: https://github.com/witnessmenow/ESP32-Cheap-Yellow-Display

## Display Concept

The dashboard is glance-first. It uses separate horizontal bars for each provider and quota.

```text
AI Usage                         WiFi OK
Updated 14:32

Codex / main
5h usage        [########------] 69% left
Weekly usage    [#########-----] 89% left

GitHub Copilot
Chat            [#########-----] 88% left
Completions     [##########----] 96% left

DeepSeek
Wallet          [##------------] $0.71 / $5.00
```

## Rendering Rules

- Providers are never combined into a single total.
- Multiple accounts for the same provider are displayed separately using the compact `al` account label. These labels come from configuration and are not fixed by the firmware.
- Each quota is rendered as its own line bar.
- Bars show remaining capacity, not used capacity.
- Percent meters use `remaining_percent` from the backend.
- DeepSeek wallet balance is rendered against `DEEPSEEK_BALANCE_TARGET_USD`.
- Unknown values render as gray bars or text-only rows.

## Status Colors

| Status | Meaning | Color |
| --- | --- | --- |
| `ok` | healthy remaining usage | green |
| `warning` | low remaining usage | yellow |
| `critical` | nearly exhausted | red |
| `unknown` | no percentage available | gray |
| `error` | provider refresh failed | red/gray with error marker |

## Data Source

The firmware should call:

```text
GET /api/v1/summary.compact
X-API-Key: <ESP32_API_KEY>
```

Only the compact endpoint should be used by the ESP32. The full endpoint is intended for richer clients.

## Refresh Behavior

Recommended device behavior:

- Fetch every 3-5 minutes.
- Keep displaying the last successful payload if a refresh fails.
- Show a small network/API status indicator in the header.
- Avoid frequent full-screen redraws; update only changed areas when possible.

## Future Touch Support

Touch can be added later for:

- Switching between overview and provider detail pages.
- Triggering a manual refresh.
- Showing reset times or provider errors.

## Local Visual Emulator

A dependency-free browser emulator is available at:

```text
dashboard/emulator/pixel-command-grid.html
```

It renders the locked 03 Pixel Command Grid theme as a 320x240 landscape preview using sample compact API data. Earlier comparison concepts are kept under `dashboard/emulator/` for reference.

## Firmware

The PlatformIO firmware skeleton lives at:

```text
dashboard/firmware/
```

Copy `dashboard/firmware/include/config.example.h` to `dashboard/firmware/include/config.h` when the board arrives, then fill in Wi-Fi and ESP32 API key values locally. The real `config.h` is ignored by git.
