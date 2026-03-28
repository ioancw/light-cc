"""Shared pytest fixtures for Light CC tests."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.db_models import Base, User
from core.auth import hash_password, create_access_token


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def test_db():
    """Create an in-memory SQLite database with all tables."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def test_user(test_db: AsyncSession) -> User:
    """Create and return a test user."""
    user = User(
        email="test@example.com",
        password_hash=hash_password("testpass123"),
        display_name="Test User",
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)
    return user


@pytest.fixture
def test_token(test_user: User) -> str:
    """Create a valid JWT for the test user."""
    return create_access_token(test_user.id, test_user.email)


@pytest.fixture
def test_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory structure."""
    workspace = tmp_path / "workspace"
    outputs = tmp_path / "outputs"
    uploads = tmp_path / "uploads"
    memory = tmp_path / "memory"
    for d in (workspace, outputs, uploads, memory):
        d.mkdir()
    # Create a test file
    (workspace / "hello.txt").write_text("hello world")
    return tmp_path
