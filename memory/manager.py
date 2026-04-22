"""Per-user memory system — DB-backed with file-based fallback.

Stores persistent memory entries in the database (memories table).
Falls back to file-based storage for backwards compatibility when
DB is not available or for the default user.
"""

from __future__ import annotations

import json
import logging
import re
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import date
from pathlib import Path
from typing import Any

from filelock import FileLock
from tools.registry import register_tool

logger = logging.getLogger(__name__)

# Default data root — used for file-based fallback and ensure_user_dirs
_DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "users"

# Allowed values for the memory_type column. Mirrors the auto-memory types
# exposed by Claude Code (note, fact, preference, project, reference) plus
# a couple of generic ones. Unknown types default to "note".
_MEMORY_TYPES = {"note", "fact", "preference", "project", "reference", "feedback", "user"}

# Context-var for async-safe per-request user identity
_current_user_id: ContextVar[str] = ContextVar("current_user_id", default="default")


def set_data_root(path: str | Path) -> None:
    global _DATA_ROOT
    _DATA_ROOT = Path(path)


def _user_dir(user_id: str) -> Path:
    return _DATA_ROOT / user_id


def _memory_dir(user_id: str) -> Path:
    return _user_dir(user_id) / "memory"


def _memory_index(user_id: str) -> Path:
    return _user_dir(user_id) / "MEMORY.md"


def ensure_user_dirs(user_id: str) -> None:
    """Create user directory structure if it doesn't exist."""
    for d in [_user_dir(user_id), _memory_dir(user_id),
              _user_dir(user_id) / "uploads", _user_dir(user_id) / "outputs"]:
        d.mkdir(parents=True, exist_ok=True)


# ── DB-backed operations ────────────────────────────────────────────────

@asynccontextmanager
async def _get_db():
    """Yield a database session, or None if DB is not available.

    Wraps ``core.database.get_db`` so callers can use ``async with _get_db() as db:``
    and fall back to file-based storage when ``db is None``.
    """
    try:
        from core.database import get_db
        cm = get_db()
    except Exception:
        yield None
        return
    try:
        session = await cm.__aenter__()
    except Exception:
        yield None
        return
    try:
        yield session
    finally:
        try:
            await cm.__aexit__(None, None, None)
        except Exception:
            pass


async def load_memory(user_id: str) -> str:
    """Load memory listing for injection into system prompt.

    Returns a listing of available memory entries so the model knows
    what's available. Individual entries can be read via ReadMemory.
    """
    async with _get_db() as db:
        if db:
            try:
                from core.db_models import Memory
                from sqlalchemy import select
                result = await db.execute(
                    select(Memory)
                    .where(Memory.user_id == user_id)
                    .order_by(Memory.created_at)
                )
                rows = list(result.scalars().all())
                if not rows:
                    return ""
                lines = []
                for m in rows:
                    tags = m.tags_list
                    tag_str = f", tags: {', '.join(tags)}" if tags else ""
                    lines.append(
                        f"- **{m.title}** (`{m.id}`, type: {m.memory_type}{tag_str})"
                    )
                return "Available memories (use ReadMemory to view full content):\n" + "\n".join(lines)
            except Exception as e:
                logger.debug(f"DB load_memory failed, falling back to files: {e}")

    # File-based fallback
    return _load_memory_files(user_id)


def _load_memory_files(user_id: str) -> str:
    """File-based fallback for load_memory."""
    ensure_user_dirs(user_id)
    mem_dir = _memory_dir(user_id)
    files = sorted(mem_dir.glob("*.md"))
    if not files:
        return ""
    lines: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8").strip()
        title = f.stem
        for line in text.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                title = stripped
                break
        lines.append(f"- **{title}** (`{f.name}`)")
    return "Available memories (use ReadMemory to view full content):\n" + "\n".join(lines)


async def save_memory(
    user_id: str,
    title: str,
    content: str,
    *,
    tags: list[str] | None = None,
    memory_type: str = "note",
    source: str = "user",
    source_conversation_id: str | None = None,
) -> str:
    """Save a memory entry. Returns the memory ID or file path.

    ``source`` is "user" for explicit tool calls and "auto" for entries
    produced by the background extraction job. ``source_conversation_id``
    records which conversation an auto-extracted memory came from.
    """
    if memory_type not in _MEMORY_TYPES:
        memory_type = "note"
    if source not in {"user", "auto"}:
        source = "user"
    clean_tags = [t.strip() for t in (tags or []) if t and t.strip()]

    async with _get_db() as db:
        if db:
            try:
                from core.db_models import Memory
                mem = Memory(
                    user_id=user_id,
                    title=title,
                    content=content,
                    memory_type=memory_type,
                    tags=json.dumps(clean_tags) if clean_tags else None,
                    source=source,
                    source_conversation_id=source_conversation_id,
                )
                db.add(mem)
                await db.commit()
                await db.refresh(mem)
                return mem.id
            except Exception as e:
                logger.debug(f"DB save_memory failed, falling back to files: {e}")

    # File-based fallback (ignores tags/type — DB is the primary path)
    return _save_memory_file(user_id, title, content)


def _save_memory_file(user_id: str, title: str, content: str) -> str:
    """File-based fallback for save_memory."""
    ensure_user_dirs(user_id)
    today = date.today().isoformat()
    slug = re.sub(r"[^\w\-]", "", title.lower().replace(" ", "-"))[:50]
    filename = f"{today}-{slug}.md"
    filepath = _memory_dir(user_id) / filename
    filepath.write_text(content, encoding="utf-8")

    index = _memory_index(user_id)
    lock = FileLock(str(index) + ".lock")
    entry = f"\n- [{title}](memory/{filename})\n"
    with lock:
        with open(index, "a", encoding="utf-8") as f:
            f.write(entry)
    return str(filepath)


async def read_memory(user_id: str, identifier: str) -> str | None:
    """Read a single memory entry by ID or filename. Returns content or None."""
    # Prevent path traversal for file-based identifiers
    if ".." in identifier or "/" in identifier or "\\" in identifier:
        return None

    async with _get_db() as db:
        if db:
            try:
                from core.db_models import Memory
                from sqlalchemy import select
                # Try by ID first
                result = await db.execute(
                    select(Memory).where(Memory.id == identifier, Memory.user_id == user_id)
                )
                mem = result.scalar_one_or_none()
                if mem:
                    return mem.content
            except Exception as e:
                logger.debug(f"DB read_memory failed, falling back to files: {e}")

    # File-based fallback
    ensure_user_dirs(user_id)
    filepath = _memory_dir(user_id) / identifier
    if filepath.exists():
        return filepath.read_text(encoding="utf-8")
    return None


async def search_memory(
    user_id: str,
    query: str,
    *,
    tags: list[str] | None = None,
    memory_type: str | None = None,
) -> list[dict[str, str]]:
    """Search memory entries by keyword, optionally filtered by tags/type.

    When ``tags`` is provided, rows are required to contain ALL given tags.
    When ``memory_type`` is provided, only rows of that type are returned.
    An empty ``query`` with non-empty filters returns all matching rows.
    """
    async with _get_db() as db:
        if db:
            try:
                from core.db_models import Memory
                from sqlalchemy import select, or_, and_
                conds = [Memory.user_id == user_id]
                if query:
                    query_pattern = f"%{query}%"
                    conds.append(or_(
                        Memory.title.ilike(query_pattern),
                        Memory.content.ilike(query_pattern),
                    ))
                if memory_type:
                    conds.append(Memory.memory_type == memory_type)
                result = await db.execute(
                    select(Memory).where(and_(*conds)).order_by(Memory.created_at)
                )
                rows = list(result.scalars().all())

                # Tag filtering is done in Python because `tags` is a JSON string
                # column — doing a proper tag query would require a side table.
                if tags:
                    want = set(tags)
                    rows = [m for m in rows if want.issubset(set(m.tags_list))]

                return [
                    {
                        "id": m.id,
                        "title": m.title,
                        "content": m.content,
                        "type": m.memory_type,
                        "tags": m.tags_list,
                        "source": m.source,
                        "source_conversation_id": m.source_conversation_id,
                    }
                    for m in rows
                ]
            except Exception as e:
                logger.debug(f"DB search_memory failed, falling back to files: {e}")

    # File-based fallback
    return _search_memory_files(user_id, query)


def _search_memory_files(user_id: str, query: str) -> list[dict[str, str]]:
    """File-based fallback for search_memory."""
    ensure_user_dirs(user_id)
    query_lower = query.lower()
    results: list[dict[str, str]] = []
    for md_file in sorted(_memory_dir(user_id).glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        if query_lower in text.lower():
            results.append({"file": md_file.name, "content": text})
    return results


async def list_memories(user_id: str) -> list[dict[str, Any]]:
    """List all memory entries with titles."""
    async with _get_db() as db:
        if db:
            try:
                from core.db_models import Memory
                from sqlalchemy import select
                result = await db.execute(
                    select(Memory)
                    .where(Memory.user_id == user_id)
                    .order_by(Memory.created_at)
                )
                rows = list(result.scalars().all())
                return [
                    {
                        "id": m.id,
                        "title": m.title,
                        "type": m.memory_type,
                        "tags": m.tags_list,
                        "source": m.source,
                        "source_conversation_id": m.source_conversation_id,
                    }
                    for m in rows
                ]
            except Exception as e:
                logger.debug(f"DB list_memories failed, falling back to files: {e}")

    # File-based fallback
    return _list_memories_files(user_id)


def _list_memories_files(user_id: str) -> list[dict[str, str]]:
    """File-based fallback for list_memories."""
    ensure_user_dirs(user_id)
    entries: list[dict[str, str]] = []
    for f in sorted(_memory_dir(user_id).glob("*.md")):
        text = f.read_text(encoding="utf-8").strip()
        title = f.stem
        for line in text.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                title = stripped
                break
        entries.append({"file": f.name, "title": title})
    return entries


async def update_memory(
    user_id: str,
    identifier: str,
    *,
    title: str | None = None,
    content: str | None = None,
    tags: list[str] | None = None,
    memory_type: str | None = None,
) -> bool:
    """Update fields on a memory entry by ID. Returns True if updated.

    Only DB-backed memories are updatable — the file fallback is append-only.
    """
    if memory_type is not None and memory_type not in _MEMORY_TYPES:
        memory_type = "note"

    async with _get_db() as db:
        if not db:
            return False
        try:
            from core.db_models import Memory
            from sqlalchemy import select
            result = await db.execute(
                select(Memory).where(
                    Memory.id == identifier, Memory.user_id == user_id,
                )
            )
            mem = result.scalar_one_or_none()
            if not mem:
                return False

            if title is not None:
                mem.title = title
            if content is not None:
                mem.content = content
            if memory_type is not None:
                mem.memory_type = memory_type
            if tags is not None:
                clean = [t.strip() for t in tags if t and t.strip()]
                mem.tags = json.dumps(clean) if clean else None

            await db.commit()
            return True
        except Exception as e:
            logger.debug(f"DB update_memory failed: {e}")
            return False


async def delete_memory(user_id: str, identifier: str) -> bool:
    """Delete a memory entry by ID. Returns True if deleted."""
    async with _get_db() as db:
        if db:
            try:
                from core.db_models import Memory
                from sqlalchemy import delete
                result = await db.execute(
                    delete(Memory).where(Memory.id == identifier, Memory.user_id == user_id)
                )
                await db.commit()
                return result.rowcount > 0
            except Exception as e:
                logger.debug(f"DB delete_memory failed: {e}")
    return False


# ── Memory tools (registered for Claude to use) ─────────────────────

def set_current_user(user_id: str) -> None:
    _current_user_id.set(user_id)


async def handle_save_memory(tool_input: dict[str, Any]) -> str:
    title = tool_input.get("title", "untitled")
    content = tool_input.get("content", "")
    tags = tool_input.get("tags") or []
    memory_type = tool_input.get("memory_type") or "note"
    if not content:
        return json.dumps({"error": "No content provided"})
    if not isinstance(tags, list):
        return json.dumps({"error": "tags must be a list of strings"})
    result = await save_memory(
        _current_user_id.get(), title, content,
        tags=tags, memory_type=memory_type,
    )
    return json.dumps({"status": "saved", "id": result})


async def handle_read_memory(tool_input: dict[str, Any]) -> str:
    identifier = tool_input.get("filename", "") or tool_input.get("id", "")
    if not identifier:
        return json.dumps({"error": "No filename or id provided"})
    content = await read_memory(_current_user_id.get(), identifier)
    if content is None:
        return json.dumps({"error": f"Memory not found: {identifier}"})
    return json.dumps({"id": identifier, "content": content})


async def handle_search_memory(tool_input: dict[str, Any]) -> str:
    query = tool_input.get("query", "")
    tags = tool_input.get("tags") or None
    memory_type = tool_input.get("memory_type") or None
    if not query and not tags and not memory_type:
        return json.dumps({"error": "Provide at least a query, tags, or memory_type"})
    if tags is not None and not isinstance(tags, list):
        return json.dumps({"error": "tags must be a list of strings"})
    results = await search_memory(
        _current_user_id.get(), query,
        tags=tags, memory_type=memory_type,
    )
    return json.dumps({"results": results, "count": len(results)})


async def handle_list_memories(tool_input: dict[str, Any]) -> str:
    memories = await list_memories(_current_user_id.get())
    return json.dumps({"memories": memories, "count": len(memories)})


async def handle_update_memory(tool_input: dict[str, Any]) -> str:
    identifier = tool_input.get("id", "")
    if not identifier:
        return json.dumps({"error": "No id provided"})
    title = tool_input.get("title")
    content = tool_input.get("content")
    tags = tool_input.get("tags")
    memory_type = tool_input.get("memory_type")
    if title is None and content is None and tags is None and memory_type is None:
        return json.dumps({"error": "No fields to update"})
    if tags is not None and not isinstance(tags, list):
        return json.dumps({"error": "tags must be a list of strings"})
    ok = await update_memory(
        _current_user_id.get(), identifier,
        title=title, content=content, tags=tags, memory_type=memory_type,
    )
    if not ok:
        return json.dumps({"error": f"Memory not found or not updatable: {identifier}"})
    return json.dumps({"status": "updated", "id": identifier})


async def handle_delete_memory(tool_input: dict[str, Any]) -> str:
    identifier = tool_input.get("id", "")
    if not identifier:
        return json.dumps({"error": "No id provided"})
    ok = await delete_memory(_current_user_id.get(), identifier)
    if not ok:
        return json.dumps({"error": f"Memory not found: {identifier}"})
    return json.dumps({"status": "deleted", "id": identifier})


_MEMORY_TYPE_ENUM = sorted(_MEMORY_TYPES)


register_tool(
    name="SaveMemory",
    aliases=["save_memory"],
    description=(
        "Save information to remember for future conversations. Use when the user shares "
        "preferences, project context, or important facts. Tag memories for later filtering; "
        "pick the memory_type that best describes the entry (defaults to 'note')."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Brief title for the memory (e.g., 'Chart preferences')",
            },
            "content": {
                "type": "string",
                "description": "The information to remember",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of tags for later filtering (e.g. ['finance', 'volatility']).",
            },
            "memory_type": {
                "type": "string",
                "enum": _MEMORY_TYPE_ENUM,
                "description": "Category for the memory. Defaults to 'note'.",
            },
        },
        "required": ["title", "content"],
    },
    handler=handle_save_memory,
)

register_tool(
    name="ReadMemory",
    aliases=["read_memory"],
    description="Read the full content of a specific memory entry. Use after ListMemories or SearchMemory to view a particular note.",
    input_schema={
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "The memory ID or filename",
            },
        },
        "required": ["filename"],
    },
    handler=handle_read_memory,
)

register_tool(
    name="SearchMemory",
    aliases=["search_memory"],
    description=(
        "Search saved memories. Provide at least one of: 'query' (keyword in title/content), "
        "'tags' (return only entries that have ALL given tags), or 'memory_type' (restrict to one type)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keyword match against title/content. May be empty if filtering by tags or type.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Require ALL of these tags to be present on the memory.",
            },
            "memory_type": {
                "type": "string",
                "enum": _MEMORY_TYPE_ENUM,
                "description": "Restrict results to a specific memory type.",
            },
        },
    },
    handler=handle_search_memory,
)

register_tool(
    name="ListMemories",
    aliases=["list_memories"],
    description="List all saved memory entries with their titles, types, and tags.",
    input_schema={
        "type": "object",
        "properties": {},
    },
    handler=handle_list_memories,
)

register_tool(
    name="UpdateMemory",
    aliases=["update_memory"],
    description=(
        "Update an existing memory entry by ID. Supply any subset of title/content/tags/memory_type. "
        "Use this to correct a memory or add tags rather than creating a duplicate."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The memory ID to update."},
            "title": {"type": "string"},
            "content": {"type": "string"},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Replaces the existing tags entirely.",
            },
            "memory_type": {"type": "string", "enum": _MEMORY_TYPE_ENUM},
        },
        "required": ["id"],
    },
    handler=handle_update_memory,
)

register_tool(
    name="DeleteMemory",
    aliases=["delete_memory"],
    description="Delete a memory entry by ID. Use when information is stale or incorrect.",
    input_schema={
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The memory ID to delete."},
        },
        "required": ["id"],
    },
    handler=handle_delete_memory,
)
