"""Scannit Platform API — FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import health, jobs, projects


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
