"""Provider adapter package.

Exposes :func:`get_adapters` which builds the set of enabled provider adapters
from settings, used by the refresh orchestrator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import ProviderAdapter, ProviderError
from .codex import CodexAdapter
from .copilot import CopilotAdapter
from .deepseek import DeepSeekAdapter
from .opencode import OpenCodeAdapter

if TYPE_CHECKING:
    from ..config import Settings

__all__ = [
    "ProviderAdapter",
    "ProviderError",
    "get_adapters",
]


def get_adapters(settings: "Settings") -> list[ProviderAdapter]:
    """Instantiate all adapters; only those ``enabled`` will fetch data."""
    return [
        CodexAdapter(settings),
        CopilotAdapter(settings),
        DeepSeekAdapter(settings),
        OpenCodeAdapter(settings),
    ]
