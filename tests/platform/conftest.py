"""Shared fixtures for platform API tests.

Uses an in-memory SQLite database for fast, isolated tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Patch settings BEFORE importing the app so it picks up the test DB URL
_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop():
    """Create a single event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def test_app():
    """Create a fresh app instance with a test database for each test."""
    # Import here to allow patching
    from app.core.config import Settings

    test_settings = Settings(database_url=_TEST_DB_URL, debug=False)

    with patch("app.core.config.settings", test_settings):
        # Re-import to pick up patched settings
        from app.models.base import Base

        test_engine = create_async_engine(_TEST_DB_URL, echo=False)
        test_session_factory = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        # Create tables
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async def get_test_session() -> AsyncIterator[AsyncSession]:
            async with test_session_factory() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        # Import and patch the app
        from app.main import app
        from app.core.database import get_session

        app.dependency_overrides[get_session] = get_test_session

        yield app

        app.dependency_overrides.clear()

        # Drop tables
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        await test_engine.dispose()


@pytest_asyncio.fixture
async def client(test_app) -> AsyncIterator[AsyncClient]:
    """Async HTTP client wired to the test app."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
