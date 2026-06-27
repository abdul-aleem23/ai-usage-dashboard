"""Provider adapter protocol.

Every provider adapter implements :meth:`ProviderAdapter.fetch_meters`, returning
fully-normalized :class:`~app.models.UsageMeter` instances for all of its
configured accounts. The refresh orchestrator drives these adapters and persists
their output.
"""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING

from ..models import UsageMeter

if TYPE_CHECKING:
    from ..config import Settings


class ProviderError(Exception):
    """Raised when a provider fetch fails in a way that should be recorded."""


class ProviderAdapter(abc.ABC):
    """Base class for all provider adapters."""

    provider_id: str = "base"

    def __init__(self, settings: "Settings") -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        """Whether this provider has enough configuration to run."""
        return True

    @abc.abstractmethod
    async def fetch_meters(self) -> list[UsageMeter]:
        """Fetch and normalize all meters for this provider."""
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release any resources (HTTP clients, etc.)."""
        return None
