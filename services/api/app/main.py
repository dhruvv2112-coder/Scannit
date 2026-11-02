"""Scannit Platform API — FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.routers import health


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup: initialise DB pool, Redis, S3 client
    yield
    # Shutdown: close connections


app = FastAPI(
    title="Scannit Platform API",
    version=settings.app_version,
    lifespan=lifespan,
)

app.include_router(health.router)
