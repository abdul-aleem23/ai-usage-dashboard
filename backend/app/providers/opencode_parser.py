"""Pure HTML parser for the OpenCode Go dashboard.

This module has **no Playwright dependency** so it can be unit-tested in
isolation from the browser fetcher. The browser module produces an HTML string;
this module turns it into structured meter data.

The parser is defensive: it searches for the text labels "Rolling Usage",
"Weekly Usage", and "Monthly Usage", then looks within the nearest container
for a percentage value and an optional reset label. It tolerates a range of
card / list / table layouts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

# Canonical meter keys -> label text searched for in the dashboard HTML.
METER_LABELS: dict[str, str] = {
    "rolling": "Rolling Usage",
    "weekly": "Weekly Usage",
    "monthly": "Monthly Usage",
}

# Matches "42%", "42.5 %", "0%" etc.
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")

# Matches "Resets in 3h 12m", "Reset at 2026-06-27", "resets in 2d 4h" etc.
_RESET_RE = re.compile(r"reset[s]?\s+(?:in|at)\b[^\n<]*", re.IGNORECASE)

# How many ancestor levels to climb when looking for a meter's container.
_MAX_CLIMB = 4


@dataclass(frozen=True)
class ParsedMeter:
    """Raw values extracted from the dashboard for a single usage window."""

    key: str
    label: str
    used_percent: int | None
    reset_label: str | None


def parse_opencode_dashboard(html: str) -> list[ParsedMeter]:
    """Parse OpenCode Go dashboard HTML into meter data.

    Returns one :class:`ParsedMeter` per recognized window (rolling/weekly/
    monthly) that is actually present in the HTML, in canonical order. Windows
    that cannot be found are omitted.
    """
    soup = BeautifulSoup(html, "html.parser")
    results: list[ParsedMeter] = []
    for key, label_text in METER_LABELS.items():
        parsed = _find_meter(soup, key, label_text)
        if parsed is not None:
            results.append(parsed)
    return results


def _find_meter(soup: BeautifulSoup, key: str, label_text: str) -> ParsedMeter | None:
    """Locate a single meter by its label and extract percentage + reset."""
    label_node = _find_label_node(soup, label_text)
    if label_node is None:
        return None

    container = _containing_block(label_node)
    if container is None:
        return None

    used_pct = _extract_percent(container)
    reset_label = _extract_reset_label(container, exclude=label_node)

    # If the percentage isn't in the container, try the immediately following
    # siblings of the label element (common in simple list/label-value layouts).
    if used_pct is None:
        used_pct = _extract_percent_from_siblings(label_node)

    return ParsedMeter(
        key=key,
        label=label_text,
        used_percent=used_pct,
        reset_label=reset_label,
    )


def _find_label_node(soup: BeautifulSoup, label_text: str) -> NavigableString | Tag | None:
    """Find the text node or element whose text contains the meter label."""
    needle = label_text.strip().lower()

    # Prefer an exact-ish text match on a leaf element so the container climb
    # starts as close to the label as possible. Skip HTML comments / doctype.
    for element in soup.find_all(string=lambda t: bool(t) and needle in t.lower()):
        if isinstance(element, Comment):
            continue
        return element
    return None


def _containing_block(node: NavigableString | Tag) -> Tag | None:
    """Climb ancestors to find a block-level container holding the meter."""
    parent = node.parent if isinstance(node, NavigableString) else node
    climbed = 0
    while parent is not None and climbed < _MAX_CLIMB:
        assert isinstance(parent, Tag)
        # Stop at a container that looks like a card / section / row.
        if _looks_like_container(parent):
            return parent
        parent = parent.parent
        climbed += 1
    # Fall back to whatever ancestor we reached.
    return parent if isinstance(parent, Tag) else None


def _looks_like_container(tag: Tag) -> bool:
    """Heuristic: does this tag look like a self-contained meter card?"""
    cls = " ".join(tag.get("class", [])).lower() if isinstance(tag.get("class"), list) else ""
    name = tag.name
    if name in ("section", "article", "li", "tr", "td"):
        return True
    if any(word in cls for word in ("card", "meter", "usage", "stat", "tile", "row", "item")):
        return True
    return False


def _extract_percent(container: Tag) -> int | None:
    """Find the first percentage value within ``container``'s text."""
    text = container.get_text(separator=" ", strip=True)
    match = _PERCENT_RE.search(text)
    if match is None:
        return None
    return _to_int(match.group(1))


def _extract_percent_from_siblings(label_node: NavigableString | Tag) -> int | None:
    """Search forward siblings of the label for a percentage (label-value layout)."""
    element = label_node.parent if isinstance(label_node, NavigableString) else label_node
    if element is None:
        return None
    sibling = element.next_sibling
    checked = 0
    while sibling is not None and checked < 4:
        text = _node_text(sibling)
        if text:
            match = _PERCENT_RE.search(text)
            if match:
                return _to_int(match.group(1))
        sibling = sibling.next_sibling
        checked += 1
    return None


def _extract_reset_label(container: Tag, exclude: NavigableString | Tag | None) -> str | None:
    """Find a 'Resets in/at ...' label within ``container``.

    Prefers a dedicated child element whose text begins with ``reset`` so the
    label does not bleed into surrounding card text. Falls back to a tight
    regex on the container's flattened text.
    """
    # Look for a descendant element whose own text starts with "reset".
    for descendant in container.find_all(string=_reset_text_filter):
        if isinstance(descendant, Comment):
            continue
        label = descendant.strip()
        if label:
            return _capitalize(label)

    # Fallback: tight regex on the full container text.
    text = container.get_text(separator=" ", strip=True)
    match = _RESET_RE.search(text)
    if match is None:
        return None
    label = re.sub(r"\s+", " ", match.group(0)).strip()
    return _capitalize(label)


def _reset_text_filter(t: str) -> bool:
    """Soup string filter: non-empty text starting with 'reset' (case-insensitive)."""
    if not t:
        return False
    return t.strip().lower().startswith("reset")


def _capitalize(label: str) -> str:
    if not label:
        return label
    return label[0].upper() + label[1:]


def _node_text(node) -> str:
    if isinstance(node, NavigableString):
        return str(node).strip()
    if isinstance(node, Tag):
        return node.get_text(separator=" ", strip=True)
    return ""


def _to_int(value: str) -> int:
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return 0
