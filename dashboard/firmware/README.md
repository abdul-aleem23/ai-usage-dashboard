# ESP32 Firmware

This is the firmware skeleton for the ESP32 Cheap Yellow Display dashboard.

## Tooling

Use PlatformIO first. It gives us reproducible libraries, board config, serial monitor, and upload commands.

Required libraries are declared in `platformio.ini`:

- `TFT_eSPI`
- `ArduinoJson`

## Local Config

Create your local config from the example:

```text
copy dashboard\firmware\include\config.example.h dashboard\firmware\include\config.h
```

Then edit `config.h`:

```cpp
#define WIFI_SSID "your-wifi-name"
#define WIFI_PASSWORD "your-wifi-password"
#define API_BASE_URL "https://ai-usage.forexstreet-bmm.com"
#define ESP32_API_KEY "esp32_replace_me"
```

`config.h` is gitignored and must not be committed.

## First Flash

From `dashboard/firmware`:

```text
pio run
pio run --target upload
pio device monitor
```

## Board Checks When It Arrives

Before debugging the app logic, confirm these basics:

- USB serial port appears when plugged in.
- Screen initializes and uses landscape rotation.
- Backlight turns on.
- Wi-Fi connects to your network.
- `/api/v1/summary.compact` returns HTTP 200.
- Rows render in this order from backend data, not hardcoded order.

## Notes

The current firmware intentionally ignores touch. Touch can be added after the read-only dashboard is stable.
