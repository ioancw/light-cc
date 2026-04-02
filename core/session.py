"""Framework-agnostic session store with DB-backed conversation persistence."""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any

from core.config import settings

logger = logging.getLogger(__name__)

# All sessions keyed by session ID (WebSocket connection UUID)
_sessions: dict[str, dict[str, Any]] = {}

# ContextVar so tools can access the current session without being passed it
_current_session_id: ContextVar[str] = ContextVar("current_session_id", default="")

# Active tool filter — set when a skill restricts available tools
_active_tool_filter: ContextVar[list[str] | None] = ContextVar("active_tool_filter", default=None)


def set_active_tool_filter(tool_names: list[str] | None) -> None:
    """Set the active skill's tool filter (or None to allow all)."""
    _active_tool_filter.set(tool_names)


def get_active_tool_filter() -> list[str] | None:
    """Get the active skill's tool filter, or None if all tools are allowed."""
    return _active_tool_filter.get()


def create_session(session_id: str, *, user_id: str = "default", conversation_id: str | None = None) -> dict[str, Any]:
    """Create a new session with default state."""
    session: dict[str, Any] = {
        "messages": [],
        "datasets": {},
        "last_figure": None,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "tasks": {},
        "permission_mode": "default",
        "active_files": [],
        "project_config": None,
        "project_rules": None,
    }
    _sessions[session_id] = session
    return session


def get_session(session_id: str) -> dict[str, Any] | None:
    return _sessions.get(session_id)


async def destroy_session_async(session_id: str) -> None:
    """Remove session from memory and Redis."""
    _sessions.pop(session_id, None)
    from core.redis_store import delete_session_state
    await delete_session_state(session_id)


def destroy_session(session_id: str) -> None:
    """Remove session from memory (sync version for non-async contexts)."""
    _sessions.pop(session_id, None)


def session_get(session_id: str, key: str) -> Any:
    """Get a value from a specific session by ID."""
    s = _sessions.get(session_id)
    return s.get(key) if s else None


def session_set(session_id: str, key: str, value: Any) -> None:
    """Set a value in a specific session by ID."""
    s = _sessions.get(session_id)
    if s is not None:
        s[key] = value


def set_current_session(session_id: str) -> None:
    _current_session_id.set(session_id)


def current_session_get(key: str) -> Any:
    """Get a value from the current session (identified by ContextVar)."""
    sid = _current_session_id.get()
    s = _sessions.get(sid)
    return s.get(key) if s else None


def current_session_set(key: str, value: Any) -> None:
    """Set a value in the current session (identified by ContextVar)."""
    sid = _current_session_id.get()
    s = _sessions.get(sid)
    if s is not None:
        s[key] = value


# ── Redis sync ────────────────────────────────────────────────────────

async def sync_session_to_redis(session_id: str) -> None:
    """Push current session state to Redis (if available)."""
    session = _sessions.get(session_id)
    if session:
        from core.redis_store import save_session_state
        await save_session_state(session_id, session)


# ── DB-backed conversation persistence ────────────────────────────────

async def save_conversation(session_id: str) -> str | None:
    """Persist the current session's messages to the database.

    Returns the conversation_id, or None if there's nothing to save.
    """
    from core.database import get_db
    from core.db_models import Conversation, Message

    session = _sessions.get(session_id)
    if not session or not session["messages"]:
        return None

    user_id = session["user_id"]
    conv_id = session.get("conversation_id")
    active_model = session.get("active_model") or settings.model

    db = await get_db()
    try:
        if conv_id is None:
            # Create a new conversation
            title = _derive_title(session["messages"])
            conv = Conversation(user_id=user_id, title=title, model=active_model)
            db.add(conv)
            await db.flush()
            conv_id = conv.id
            session["conversation_id"] = conv_id
        else:
            # Update existing conversation timestamp and model (preserve title)
            from sqlalchemy import select, update
            await db.execute(
                update(Conversation)
                .where(Conversation.id == conv_id)
                .values(model=active_model)
            )
            # Delete old messages and re-insert (simpler than diffing)
            from sqlalchemy import delete
            await db.execute(delete(Message).where(Message.conversation_id == conv_id))

        # Insert all messages
        for msg in session["messages"]:
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
        # Try to parse JSON content (for tool results etc.)
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            pass
        messages.append({"role": row.role, "content": content})

    return messages


async def fork_conversation(source_conv_id: str, user_id: str) -> tuple[str, list[dict[str, Any]]]:
    """Fork a conversation: copy all messages into a new Conversation row.

    Returns (new_conv_id, messages).
    """
    messages = await load_conversation(source_conv_id)
    if not messages:
        raise ValueError(f"No messages found for conversation {source_conv_id}")

    from core.database import get_db
    from core.db_models import Conversation, Message

    db = await get_db()
    try:
        # Create new conversation
        title = _derive_title(messages) + " (fork)"
        conv = Conversation(user_id=user_id, title=title, model=settings.model)
        db.add(conv)
        await db.flush()
        new_conv_id = conv.id

        # Copy messages
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
                # Extract text from content blocks
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
