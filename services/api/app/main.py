"""Scannit Platform API — FastAPI application entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import artifacts, health, jobs, projects

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup: create database tables when Postgres is available.
    # Gracefully skip when running without a database (unit tests, CI).
    try:
        from app.core.database import engine
        from app.models.base import Base
        from app.models import tables  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created/verified")
    except Exception as exc:
        logger.warning("Database not available, skipping table init: %s", exc)
    yield
    # Shutdown: dispose engine connection pool if it was created
    try:
        from app.core.database import engine as _engine

        await _engine.dispose()
    except Exception:
        pass


app = FastAPI(
    title="Scannit Platform API",
    version=settings.app_version,
    lifespan=lifespan,
)

# CORS — allow local Vite viewer (dev) and configurable origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(projects.router)
app.include_router(jobs.router)
app.include_router(artifacts.router)
