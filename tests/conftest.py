"""Shared pytest fixtures for Light CC tests."""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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


# ── Database ────────────────────────────────────────────────────────────

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


# ── Mock Anthropic client ───────────────────────────────────────────────

@dataclass
class MockContentBlock:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""


@dataclass
class MockDelta:
    type: str
    text: str = ""
    partial_json: str = ""


@dataclass
class MockStreamEvent:
    type: str
    index: int = 0
    content_block: MockContentBlock | None = None
    delta: MockDelta | None = None


@dataclass
class MockUsage:
    input_tokens: int = 100
    output_tokens: int = 50


@dataclass
class MockFinalMessage:
    usage: MockUsage = field(default_factory=MockUsage)


def _build_text_events(text: str, index: int = 0) -> list[MockStreamEvent]:
    """Build streaming events for a plain text response."""
    return [
        MockStreamEvent(
            type="content_block_start",
            index=index,
            content_block=MockContentBlock(type="text"),
        ),
        MockStreamEvent(
            type="content_block_delta",
            index=index,
            delta=MockDelta(type="text_delta", text=text),
        ),
        MockStreamEvent(type="content_block_stop", index=index),
    ]


def _build_tool_events(
    tool_id: str, tool_name: str, tool_input: dict, index: int = 0,
) -> list[MockStreamEvent]:
    """Build streaming events for a tool_use block."""
    return [
        MockStreamEvent(
            type="content_block_start",
            index=index,
            content_block=MockContentBlock(type="tool_use", id=tool_id, name=tool_name),
        ),
        MockStreamEvent(
            type="content_block_delta",
            index=index,
            delta=MockDelta(type="input_json_delta", partial_json=json.dumps(tool_input)),
        ),
        MockStreamEvent(type="content_block_stop", index=index),
    ]


class MockStream:
    """Mock for client.messages.stream() async context manager."""

    def __init__(self, events: list[MockStreamEvent], final_message: MockFinalMessage | None = None):
        self._events = events
        self._final = final_message or MockFinalMessage()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def __aiter__(self):
        for event in self._events:
            yield event

    async def get_final_message(self):
        return self._final


@pytest.fixture
def mock_anthropic_client():
    """Fixture that patches get_client() and provides a configurable mock.

    Usage:
        def test_something(mock_anthropic_client):
            client, set_responses = mock_anthropic_client
            # Set up a text-only response
            set_responses([_build_text_events("Hello!")])
            # Now call agent.run() -- it will get this mock client
    """
    client = MagicMock()
    response_queue: list[MockStream] = []

    def set_responses(streams: list[list[MockStreamEvent]]):
        """Set up a sequence of streaming responses (one per agent turn)."""
        response_queue.clear()
        for events in streams:
            response_queue.append(MockStream(events))

    def _make_stream(**kwargs):
        if response_queue:
            return response_queue.pop(0)
        # Default: return empty text response
        return MockStream(_build_text_events(""))

    client.messages.stream = MagicMock(side_effect=_make_stream)

    # Ensure core.agent is importable before patch.object resolves the target.
    import core.agent  # noqa: F401

    with patch("core.agent.get_client", return_value=client):
        yield client, set_responses


# ── Session state fixtures ──────────────────────────────────────────────

@pytest.fixture
def test_session():
    """Pre-populated session state with known IDs, cleaned up after.

    Returns (session_id, cid, connection, conv_session).
    """
    from core import session as sess

    session_id = "test-session-001"
    cid = "test-cid-001"

    conn = sess.create_connection(session_id, user_id="test-user-001")
    conv = sess.create_conv_session(cid, session_id)

    yield session_id, cid, conn, conv

    # Cleanup
    sess.destroy_connection(session_id)


@pytest.fixture
def clean_sessions():
    """Clear all session state before and after the test."""
    from core import session as sess

    sess._connections.clear()
    sess._conn_convs.clear()
    sess._conv_sessions.clear()
    yield
    sess._connections.clear()
    sess._conn_convs.clear()
    sess._conv_sessions.clear()


# ── Tool registry fixtures ──────────────────────────────────────────────

@pytest.fixture
def clean_tool_registry():
    """Snapshot and restore the tool registry around a test."""
    from tools import registry

    original_tools = dict(registry._TOOLS)
    original_aliases = dict(registry._ALIASES)
    yield
    registry._TOOLS.clear()
    registry._TOOLS.update(original_tools)
    registry._ALIASES.clear()
    registry._ALIASES.update(original_aliases)


# ── Hooks fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def clean_hooks():
    """Clear hooks before and after the test."""
    from core.hooks import _hooks

    _hooks.clear()
    yield
    _hooks.clear()


# ── Redis mock fixtures ────────────────────────────────────────────────

@pytest.fixture
def mock_redis():
    """Mock Redis with an in-memory dict store.

    Patches core.redis_store._pool so Redis operations work without a server.
    """
    store: dict[str, str] = {}
    ttls: dict[str, int] = {}

    sets: dict[str, set[str]] = {}
    pool = AsyncMock()

    async def _setex(key, ttl, value):
        store[key] = value
        ttls[key] = ttl

    async def _get(key):
        return store.get(key)

    async def _delete(*keys):
        for k in keys:
            store.pop(k, None)
            ttls.pop(k, None)

    async def _ping():
        return True

    async def _publish(channel, message):
        pass

    async def _sadd(key, *values):
        if key not in sets:
            sets[key] = set()
        sets[key].update(values)
        return len(values)

    async def _sismember(key, value):
        return value in sets.get(key, set())

    async def _srem(key, *values):
        s = sets.get(key, set())
        removed = len(s & set(values))
        s -= set(values)
        return removed

    async def _expire(key, ttl):
        ttls[key] = ttl
        return True

    pool.setex = AsyncMock(side_effect=_setex)
    pool.get = AsyncMock(side_effect=_get)
    pool.delete = AsyncMock(side_effect=_delete)
    pool.ping = AsyncMock(side_effect=_ping)
    pool.publish = AsyncMock(side_effect=_publish)
    pool.sadd = AsyncMock(side_effect=_sadd)
    pool.sismember = AsyncMock(side_effect=_sismember)
    pool.srem = AsyncMock(side_effect=_srem)
    pool.expire = AsyncMock(side_effect=_expire)

    with patch("core.redis_store._pool", pool):
        yield pool, store
