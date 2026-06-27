"""APScheduler-based periodic refresh.

A single :class:`AsyncIOScheduler` runs :func:`refresh_all` every
``refresh_interval_minutes``. It is started on FastAPI startup and shut down on
lifespan teardown.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

if TYPE_CHECKING:
    from .config import Settings

log = logging.getLogger(__name__)


def create_scheduler(settings: "Settings") -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _scheduled_refresh,
        args=[settings],
        trigger=IntervalTrigger(minutes=settings.refresh_interval_minutes),
        id="refresh_all",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler


async def _scheduled_refresh(settings: "Settings") -> None:
    """Wrapper that imports lazily and swallows exceptions for the scheduler."""
    from .refresh import refresh_all

    try:
        await refresh_all(settings)
    except Exception:  # noqa: BLE001
        log.exception("scheduled refresh failed")
