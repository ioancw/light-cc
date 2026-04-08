"""Framework-agnostic session store with DB-backed conversation persistence.

Two-tier model:
  - Connection state (per WebSocket): user_id, permission_mode, system prompt, project config
  - Conversation state (per cid): messages, conversation_id, active_model, datasets, etc.

Multiple conversations can run in parallel on a single WebSocket connection.

Conversation state uses a write-through cache: local dict for fast access,
async flush to Redis for cross-instance recovery. DataFrames in 'datasets'
remain node-affine (not serialized to Redis).
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextvars import ContextVar
from typing import Any

from core.config import settings

logger = logging.getLogger(__name__)

# ── Connection-level state (one per WebSocket) ───────────────────────

_connections: dict[str, dict[str, Any]] = {}  # session_id -> connection state
_conn_convs: dict[str, set[str]] = {}         # session_id -> set of cids

# ── Conversation-level state (one per cid) ───────────────────────────

_conv_sessions: dict[str, dict[str, Any]] = {}  # cid -> conversation state

# ── Dirty tracking for Redis write-through ──────────────────────────

_dirty_cids: set[str] = set()
_flush_task: asyncio.Task | None = None
_FLUSH_INTERVAL = 5  # seconds

# ── ContextVars (per async task) ─────────────────────────────────────

_current_session_id: ContextVar[str] = ContextVar("current_session_id", default="")
_current_cid: ContextVar[str] = ContextVar("current_cid", default="")
_active_tool_filter: ContextVar[list[str] | None] = ContextVar("active_tool_filter", default=None)

# ── Tool filter ──────────────────────────────────────────────────────

def set_active_tool_filter(tool_names: list[str] | None) -> None:
    _active_tool_filter.set(tool_names)


def get_active_tool_filter() -> list[str] | None:
    return _active_tool_filter.get()


# ── Connection management ────────────────────────────────────────────

def create_connection(session_id: str, *, user_id: str = "default") -> dict[str, Any]:
    """Create connection-level state for a new WebSocket."""
    conn: dict[str, Any] = {
        "user_id": user_id,
        "permission_mode": "default",
        "user_system_prompt": "",
        "project_config": None,
        "project_rules": None,
    }
    _connections[session_id] = conn
    _conn_convs[session_id] = set()

    return conn


def get_connection(session_id: str) -> dict[str, Any] | None:
    return _connections.get(session_id)


def connection_get(session_id: str, key: str) -> Any:
    c = _connections.get(session_id)
    return c.get(key) if c else None


def connection_set(session_id: str, key: str, value: Any) -> None:
    c = _connections.get(session_id)
    if c is not None:
        c[key] = value


def destroy_connection(session_id: str) -> None:
    """Remove connection and all its conversation sub-sessions."""
    for cid in list(_conn_convs.get(session_id, [])):
        _conv_sessions.pop(cid, None)
    _conn_convs.pop(session_id, None)
    _connections.pop(session_id, None)


async def destroy_connection_async(session_id: str) -> None:
    destroy_connection(session_id)
    from core.redis_store import delete_session_state
    await delete_session_state(session_id)


# ── Conversation sub-session management ──────────────────────────────

_CONV_DEFAULTS = {
    "messages": [],
    "datasets": {},
    "last_figure": None,
    "conversation_id": None,
    "active_model": None,
    "tasks": {},
    "active_files": [],
}


def create_conv_session(cid: str, session_id: str) -> dict[str, Any]:
    """Create a conversation sub-session linked to a connection."""
    conv: dict[str, Any] = {
        **{k: (v.copy() if isinstance(v, (list, dict)) else v) for k, v in _CONV_DEFAULTS.items()},
        "_conn_id": session_id,
    }
    _conv_sessions[cid] = conv
    _conn_convs.setdefault(session_id, set()).add(cid)
    return conv


def get_or_create_conv_session(cid: str, session_id: str) -> dict[str, Any]:
    """Get an existing conversation sub-session or lazily create one.

    Checks local dict first, then attempts Redis recovery before creating new.
    """
    if cid in _conv_sessions:
        return _conv_sessions[cid]

    # Try Redis recovery (async called from sync — schedule if possible)
    # Note: Redis recovery is best-effort; if not available, create fresh.
    # For full async recovery, use get_or_create_conv_session_async().
    return create_conv_session(cid, session_id)


async def get_or_create_conv_session_async(cid: str, session_id: str) -> dict[str, Any]:
    """Async version that attempts Redis recovery before creating a new session."""
    if cid in _conv_sessions:
        return _conv_sessions[cid]

    # Try Redis recovery
    from core.redis_store import load_conv_session
    cached = await load_conv_session(cid)
    if cached:
        conv = {
            **{k: (v.copy() if isinstance(v, (list, dict)) else v) for k, v in _CONV_DEFAULTS.items()},
            "_conn_id": session_id,
        }
        # Restore cached state (overwrite defaults with Redis values)
        for key, val in cached.items():
            if key in conv:
                conv[key] = val
        _conv_sessions[cid] = conv
        _conn_convs.setdefault(session_id, set()).add(cid)
        logger.debug("Recovered conversation %s from Redis", cid)
        return conv

    return create_conv_session(cid, session_id)


def get_conv_session(cid: str) -> dict[str, Any] | None:
    return _conv_sessions.get(cid)


def conv_session_get(cid: str, key: str) -> Any:
    s = _conv_sessions.get(cid)
    return s.get(key) if s else None


def conv_session_set(cid: str, key: str, value: Any) -> None:
    s = _conv_sessions.get(cid)
    if s is not None:
        s[key] = value
        _dirty_cids.add(cid)


def destroy_conv_session(cid: str) -> None:
    cs = _conv_sessions.pop(cid, None)
    _dirty_cids.discard(cid)
    if cs:
        conn_id = cs.get("_conn_id")
        if conn_id and conn_id in _conn_convs:
            _conn_convs[conn_id].discard(cid)


async def destroy_conv_session_async(cid: str) -> None:
    """Destroy a conversation session and clean up Redis."""
    destroy_conv_session(cid)
    from core.redis_store import delete_conv_session
    await delete_conv_session(cid)


def get_connection_cids(session_id: str) -> set[str]:
    """Return the set of active cids for a connection."""
    return _conn_convs.get(session_id, set()).copy()


# ── ContextVar accessors ─────────────────────────────────────────────

def set_current_session(session_id: str) -> None:
    _current_session_id.set(session_id)


def set_current_cid(cid: str) -> None:
    _current_cid.set(cid)


def current_session_get(key: str) -> Any:
    """Get a value, checking conversation state first, then connection state.

    This ensures existing tool code that calls current_session_get("user_id")
    or current_session_get("messages") keeps working without changes.
    """
    # Try conversation-level first
    cid = _current_cid.get()
    if cid:
        cs = _conv_sessions.get(cid)
        if cs and key in cs:
            return cs[key]

    # Fall back to connection-level
    sid = _current_session_id.get()
    c = _connections.get(sid)
    if c and key in c:
        return c[key]

    return None


def current_session_set(key: str, value: Any) -> None:
    """Set a value, targeting conversation state if the key belongs there."""
    cid = _current_cid.get()
    if cid:
        cs = _conv_sessions.get(cid)
        if cs and key in cs:
            cs[key] = value
            return

    sid = _current_session_id.get()
    c = _connections.get(sid)
    if c and key in c:
        c[key] = value


# ── Redis sync ────────────────────────────────────────────────────────

async def sync_session_to_redis(session_id: str) -> None:
    session = _connections.get(session_id)
    if session:
        from core.redis_store import save_session_state
        await save_session_state(session_id, session)


async def _flush_dirty_sessions() -> None:
    """Periodically flush dirty conversation sessions to Redis."""
    from core.redis_store import save_conv_session, is_available

    while True:
        try:
            await asyncio.sleep(_FLUSH_INTERVAL)
            if not is_available():
                continue
            cids = list(_dirty_cids)
            _dirty_cids.clear()
            for cid in cids:
                cs = _conv_sessions.get(cid)
                if cs:
                    await save_conv_session(cid, cs)
        except asyncio.CancelledError:
            # Final flush on shutdown
            if is_available():
                for cid in list(_dirty_cids):
                    cs = _conv_sessions.get(cid)
                    if cs:
                        await save_conv_session(cid, cs)
                _dirty_cids.clear()
            raise
        except Exception as e:
            logger.debug(f"Flush dirty sessions failed: {e}")


async def start_session_flush() -> None:
    """Start the background task that flushes dirty sessions to Redis."""
    global _flush_task
    if _flush_task is None or _flush_task.done():
        _flush_task = asyncio.create_task(_flush_dirty_sessions())
        logger.debug("Session flush task started (interval: %ds)", _FLUSH_INTERVAL)


async def stop_session_flush() -> None:
    """Stop the background flush task."""
    global _flush_task
    if _flush_task and not _flush_task.done():
        _flush_task.cancel()
        try:
            await _flush_task
        except asyncio.CancelledError:
            pass
        _flush_task = None


# ── Per-cid save locks (prevent interleaved delete+insert) ───────────

_save_locks: dict[str, asyncio.Lock] = {}

# ── DB-backed conversation persistence ────────────────────────────────

async def save_conversation(cid: str) -> str | None:
    """Persist a conversation sub-session's messages to the database.

    Accepts a cid (conversation sub-session key).
    Returns the conversation_id, or None if there's nothing to save.
    """
    from core.database import get_db
    from core.db_models import Conversation, Message

    cs = _conv_sessions.get(cid)
    if not cs or not cs.get("messages"):
        return None

    lock = _save_locks.setdefault(cid, asyncio.Lock())
    async with lock:
        conn_id = cs.get("_conn_id")
        user_id = connection_get(conn_id, "user_id") if conn_id else "default"
        conv_id = cs.get("conversation_id")
        active_model = cs.get("active_model") or settings.model

        db = await get_db()
        try:
            if conv_id is None:
                title = _derive_title(cs["messages"])
                conv = Conversation(user_id=user_id, title=title, model=active_model)
                db.add(conv)
                await db.flush()
                conv_id = conv.id
                cs["conversation_id"] = conv_id
            else:
                from sqlalchemy import update
                await db.execute(
                    update(Conversation)
                    .where(Conversation.id == conv_id)
                    .values(model=active_model)
                )
                from sqlalchemy import delete
                await db.execute(delete(Message).where(Message.conversation_id == conv_id))

            for msg in cs["messages"]:
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = json.dumps(content)
                db.add(Message(
                    conversation_id=conv_id,
                    role=msg["role"],
                    content=content if isinstance(content, str) else json.dumps(content),
                ))

            await db.commit()
        finally:
            await db.close()

        return conv_id


async def load_conversation(conversation_id: str) -> list[dict[str, Any]]:
    """Load messages from the database for a given conversation."""
    from core.database import get_db
    from core.db_models import Message
    from sqlalchemy import select

    db = await get_db()
    try:
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )
        rows = result.scalars().all()
    finally:
        await db.close()

    messages = []
    for row in rows:
        content = row.content
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            pass
        messages.append({"role": row.role, "content": content})

    return messages


async def fork_conversation(source_conv_id: str, user_id: str) -> tuple[str, list[dict[str, Any]]]:
    """Fork a conversation: copy all messages into a new Conversation row."""
    lock = _save_locks.setdefault(source_conv_id, asyncio.Lock())
    async with lock:
        messages = await load_conversation(source_conv_id)
        if not messages:
            raise ValueError(f"No messages found for conversation {source_conv_id}")

        from core.database import get_db
        from core.db_models import Conversation, Message

        db = await get_db()
        try:
            title = _derive_title(messages) + " (fork)"
            conv = Conversation(user_id=user_id, title=title, model=settings.model)
            db.add(conv)
            await db.flush()
            new_conv_id = conv.id

            for msg in messages:
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = json.dumps(content)
                db.add(Message(
                    conversation_id=new_conv_id,
                    role=msg["role"],
                    content=content if isinstance(content, str) else json.dumps(content),
                ))

            await db.commit()
        finally:
            await db.close()

        return new_conv_id, messages


def _derive_title(messages: list[dict]) -> str:
    """Derive a conversation title from the first user message."""
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        content = block["text"]
                        break
                else:
                    content = ""
            if isinstance(content, str) and content.strip():
                title = content.strip()[:80]
                if len(content.strip()) > 80:
                    title += "..."
                return title
    return "New conversation"
