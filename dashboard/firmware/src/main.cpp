#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <TFT_eSPI.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>

#include "config.h"

TFT_eSPI tft;

struct Meter {
  String provider;
  String account;
  String label;
  int remaining;
  String status;
  String reset;
};

Meter meters[8];
size_t meterCount = 0;
unsigned long lastFetchMs = 0;
bool apiOk = false;

const uint16_t C_BG = TFT_BLACK;
const uint16_t C_PANEL = 0x08A2;
const uint16_t C_PANEL_ALT = 0x1104;
const uint16_t C_BORDER = 0x2D8C;
const uint16_t C_TEXT = 0xDFFF;
const uint16_t C_MUTED = 0x6D54;
const uint16_t C_CODEX = 0x06FF;
const uint16_t C_COPILOT = 0x47E0;
const uint16_t C_DEEPSEEK = 0xFEA0;
const uint16_t C_ERROR = 0xF82A;

uint16_t meterColor(const Meter& meter) {
  if (meter.status == "warning") return C_DEEPSEEK;
  if (meter.status == "critical" || meter.status == "error") return C_ERROR;
  if (meter.provider == "codex") return C_CODEX;
  if (meter.provider == "copilot") return C_COPILOT;
  if (meter.provider == "deepseek") return C_DEEPSEEK;
  return C_TEXT;
}

String providerName(const String& provider) {
  if (provider == "codex") return "Codex";
  if (provider == "copilot") return "Copilot";
  if (provider == "deepseek") return "DeepSeek";
  return provider;
}

String fitText(String value, size_t maxLen) {
  if (value.length() <= maxLen) return value;
  return value.substring(0, maxLen - 1) + "~";
}

void drawLineBar(int x, int y, int w, int h, int percent, uint16_t color) {
  int clamped = constrain(percent, 0, 100);
  int fillW = (w * clamped) / 100;
  tft.fillRect(x, y, w, h, 0x10E5);
  tft.fillRect(x, y, fillW, h, color);
  tft.drawRect(x, y, w, h, C_BORDER);
}

void drawFrame() {
  tft.fillScreen(C_BG);
  tft.drawRect(5, 5, 310, 230, C_BORDER);
  tft.drawRect(8, 8, 304, 224, 0x1966);

  tft.fillRect(11, 11, 298, 20, 0x1124);
  tft.fillRect(11, 31, 298, 2, C_CODEX);
  tft.setTextDatum(TL_DATUM);
  tft.setTextColor(C_TEXT, 0x1124);
  tft.drawString("AI Usage Monitor", 16, 15, 2);
  tft.setTextColor(apiOk ? C_COPILOT : C_ERROR, 0x1124);
  tft.drawString(apiOk ? "WiFi OK" : "Offline", 234, 16, 1);
}

void drawMeters() {
  for (size_t i = 0; i < meterCount && i < 5; i++) {
    const Meter& meter = meters[i];
    int y = 42 + i * 33;
    uint16_t color = meterColor(meter);

    tft.fillRect(11, y - 2, 298, 29, i % 2 ? C_PANEL_ALT : C_PANEL);
    tft.fillRect(11, y - 2, 3, 29, color);

    tft.setTextDatum(TL_DATUM);
    tft.setTextColor(color, i % 2 ? C_PANEL_ALT : C_PANEL);
    tft.drawString(providerName(meter.provider), 19, y, 1);

    tft.setTextColor(C_TEXT, i % 2 ? C_PANEL_ALT : C_PANEL);
    tft.drawString(fitText(meter.account + " / " + meter.label, 18), 19, y + 12, 1);

    tft.setTextDatum(TR_DATUM);
    tft.setTextColor(color, i % 2 ? C_PANEL_ALT : C_PANEL);
    tft.drawString(String(meter.remaining) + "% left", 126, y, 1);

    drawLineBar(137, y + 2, 102, 10, meter.remaining, color);

    tft.setTextDatum(TL_DATUM);
    tft.setTextColor(color, i % 2 ? C_PANEL_ALT : C_PANEL);
    tft.drawString(meter.status == "warning" ? "LOW" : "OK", 249, y, 1);

    tft.setTextDatum(TR_DATUM);
    tft.setTextColor(C_MUTED, i % 2 ? C_PANEL_ALT : C_PANEL);
    tft.drawString(fitText(meter.reset, 10), 304, y + 12, 1);
  }
}

void drawFooter() {
  tft.drawFastHLine(11, 214, 298, C_BORDER);
  tft.setTextDatum(TL_DATUM);
  tft.setTextColor(C_MUTED, C_BG);
  tft.drawString("Endpoint: /summary.compact", 16, 220, 1);
  tft.setTextDatum(TR_DATUM);
  tft.setTextColor(C_DEEPSEEK, C_BG);
  tft.drawString("Refresh 3m", 304, 220, 1);
}

void drawDashboard() {
  drawFrame();
  drawMeters();
  drawFooter();
}

bool fetchSummary() {
  if (WiFi.status() != WL_CONNECTED) {
    apiOk = false;
    return false;
  }

  WiFiClientSecure client;
  client.setInsecure();

  HTTPClient http;
  String url = String(API_BASE_URL) + "/api/v1/summary.compact";
  http.begin(client, url);
  http.addHeader("X-API-Key", ESP32_API_KEY);

  int code = http.GET();
  if (code != 200) {
    http.end();
    apiOk = false;
    return false;
  }

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, http.getStream());
  http.end();

  if (err) {
    apiOk = false;
    return false;
  }

  meterCount = 0;
  for (JsonObject item : doc["m"].as<JsonArray>()) {
    if (meterCount >= 8) break;
    Meter& meter = meters[meterCount++];
    meter.provider = item["p"] | "unknown";
    meter.account = item["al"] | "default";
    meter.label = item["l"] | "Usage";
    meter.remaining = item["r"] | 0;
    meter.status = item["s"] | "unknown";
    meter.reset = item["rt"] | "--";
  }

  apiOk = true;
  return true;
}

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  tft.fillScreen(C_BG);
  tft.setTextColor(C_TEXT, C_BG);
  tft.drawString("Connecting WiFi...", 20, 110, 2);

  unsigned long started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < 15000) {
    delay(250);
  }
}

void setup() {
  Serial.begin(115200);
  tft.init();
  tft.setRotation(1);
  tft.setTextWrap(false);
  connectWifi();
  fetchSummary();
  drawDashboard();
  lastFetchMs = millis();
}

void loop() {
  if (millis() - lastFetchMs >= REFRESH_INTERVAL_MS) {
    fetchSummary();
    drawDashboard();
    lastFetchMs = millis();
  }
}


