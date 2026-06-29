# Dashboard Emulator

This is a browser-only 320x240 preview for the ESP32 Cheap Yellow Display dashboard. It is intentionally dependency-free: open the HTML files directly in a browser.

## Files

- `index.html` - original Doom-inspired retro HUD. Keep this as the baseline.
- `retro.html` - focused 8-bit / retro HUD finalists.
- `pixel-command-grid.html` - locked 03 Pixel Command Grid design for continued ESP32 dashboard work.
- `variants.html` - broader retro, cyberpunk, and sci-fi visual directions for comparison.
- `modern.html` - shelved non-8-bit alternatives kept only for reference.

## What It Tests

- landscape 320x240 canvas
- theme and color direction
- segmented remaining-usage bars
- compact labels matching `/api/v1/summary.compact`
- normal, warning, provider-error, and offline states

The firmware renderer should later port the same layout concepts to `TFT_eSPI` rectangles and text.

