"""Parser tests for the OpenCode Go dashboard HTML.

These tests exercise the pure :mod:`app.providers.opencode_parser` module only
— no Playwright, no browser, no network. The browser fetcher
(:mod:`app.providers.opencode_browser`) is covered separately by integration
testing at deploy time.
"""

from __future__ import annotations

from pathlib import Path

from app.providers.opencode_parser import parse_opencode_dashboard

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


# --- Card layout fixture --------------------------------------------------


def test_parse_card_layout_extracts_all_three():
    html = _load("opencode_dashboard.html")
    meters = parse_opencode_dashboard(html)

    assert len(meters) == 3
    by_key = {m.key: m for m in meters}
    assert set(by_key) == {"rolling", "weekly", "monthly"}

    rolling = by_key["rolling"]
    assert rolling.label == "Rolling Usage"
    assert rolling.used_percent == 2
    assert rolling.reset_label == "Resets in 3h 12m"

    weekly = by_key["weekly"]
    assert weekly.used_percent == 12
    assert weekly.reset_label == "Resets in 2d 4h"

    monthly = by_key["monthly"]
    assert monthly.used_percent == 47
    assert monthly.reset_label == "Resets at 2026-07-01"


# --- List layout, missing card -------------------------------------------


def test_parse_list_layout_partial():
    html = _load("opencode_dashboard_partial.html")
    meters = parse_opencode_dashboard(html)

    # Monthly Usage is absent from this fixture.
    assert len(meters) == 2
    by_key = {m.key: m for m in meters}
    assert "rolling" in by_key
    assert "weekly" in by_key
    assert "monthly" not in by_key

    rolling = by_key["rolling"]
    assert rolling.used_percent == 0  # 0.5% -> int(0.5) == 0
    assert rolling.reset_label == "Resets in 47m"

    weekly = by_key["weekly"]
    assert weekly.used_percent == 88
    assert weekly.reset_label == "Resets in 1d 6h"


# --- Edge cases ----------------------------------------------------------


def test_parse_empty_html_returns_nothing():
    assert parse_opencode_dashboard("") == []


def test_parse_html_without_usage_labels():
    html = "<html><body><h1>Hello</h1><p>50%</p></body></html>"
    assert parse_opencode_dashboard(html) == []


def test_parse_percentage_without_reset_label():
    html = """
    <html><body>
      <div class="card">
        <h3>Rolling Usage</h3>
        <div>33%</div>
      </div>
    </body></html>
    """
    meters = parse_opencode_dashboard(html)
    assert len(meters) == 1
    assert meters[0].key == "rolling"
    assert meters[0].used_percent == 33
    assert meters[0].reset_label is None


def test_parse_decimal_percentage_rounds_to_int():
    html = """
    <html><body>
      <div class="card">
        <h3>Monthly Usage</h3>
        <div>67.8%</div>
      </div>
    </body></html>
    """
    meters = parse_opencode_dashboard(html)
    assert len(meters) == 1
    assert meters[0].used_percent == 68  # int(float("67.8"))


def test_parse_ignores_promo_percentage_outside_cards():
    """A stray '50%' promo line should not pollute a meter that has no value."""
    html = """
    <html><body>
      <ul>
        <li><span>Rolling Usage</span><span>15%</span></li>
      </ul>
      <div class="promo">Get 50% more quota!</div>
    </body></html>
    """
    meters = parse_opencode_dashboard(html)
    assert len(meters) == 1
    assert meters[0].key == "rolling"
    assert meters[0].used_percent == 15


def test_parse_case_insensitive_label_match():
    html = """
    <html><body>
      <div class="card">
        <h3>rolling usage</h3>
        <div>9%</div>
        <div>resets in 10m</div>
      </div>
    </body></html>
    """
    meters = parse_opencode_dashboard(html)
    assert len(meters) == 1
    assert meters[0].key == "rolling"
    assert meters[0].used_percent == 9
    assert meters[0].reset_label == "Resets in 10m"
