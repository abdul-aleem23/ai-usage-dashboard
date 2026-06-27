"""FastAPI application entrypoint.

Wires settings, DB init, routers, the API-key dependencies and the refresh
scheduler. Settings are loaded once and stored in app state so the dependency
overrides in tests can swap them.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from . import __version__
from .config import Settings
from .db import init_db
from .routers import admin, health, providers, summary
from .scheduler import create_scheduler


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]

    configure_logging(settings.log_level)
    init_db(settings.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        scheduler = create_scheduler(settings)
        scheduler.start()
        if settings.refresh_on_startup:
            from .refresh import refresh_all

            await refresh_all(settings)
        try:
            yield
        finally:
            scheduler.shutdown(wait=False)

    app = FastAPI(
        title="AI Usage Backend",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.settings = settings

    app.include_router(health.router)
    app.include_router(summary.router)
    app.include_router(providers.router)
    app.include_router(admin.router)

    return app


# Module-level app for ``uvicorn app.main:app``.
app = create_app()
