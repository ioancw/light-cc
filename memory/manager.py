"""Per-user memory system — DB-backed with file-based fallback.

Stores persistent memory entries in the database (memories table).
Falls back to file-based storage for backwards compatibility when
DB is not available or for the default user.
"""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from datetime import date
from pathlib import Path
from typing import Any

from filelock import FileLock
from tools.registry import register_tool

logger = logging.getLogger(__name__)

# Default data root — used for file-based fallback and ensure_user_dirs
_DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "users"

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

async def _get_db():
    """Get a database session. Returns None if DB is not available."""
    try:
        from core.database import get_db
        return await get_db()
    except Exception:
        return None


async def load_memory(user_id: str) -> str:
    """Load memory listing for injection into system prompt.

    Returns a listing of available memory entries so the model knows
    what's available. Individual entries can be read via ReadMemory.
    """
    db = await _get_db()
    if db:
        try:
            from core.db_models import Memory
            from sqlalchemy import select
            result = await db.execute(
                select(Memory.id, Memory.title, Memory.memory_type)
                .where(Memory.user_id == user_id)
                .order_by(Memory.created_at)
            )
            rows = result.all()
            await db.close()
            if not rows:
                return ""
            lines = [f"- **{row.title}** (`{row.id}`, type: {row.memory_type})" for row in rows]
            return "Available memories (use ReadMemory to view full content):\n" + "\n".join(lines)
        except Exception as e:
            logger.debug(f"DB load_memory failed, falling back to files: {e}")
            try:
                await db.close()
            except Exception:
                pass

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


async def save_memory(user_id: str, title: str, content: str) -> str:
    """Save a memory entry. Returns the memory ID or file path."""
    db = await _get_db()
    if db:
        try:
            from core.db_models import Memory
            mem = Memory(user_id=user_id, title=title, content=content)
            db.add(mem)
            await db.commit()
            await db.refresh(mem)
            mem_id = mem.id
            await db.close()
            return mem_id
        except Exception as e:
            logger.debug(f"DB save_memory failed, falling back to files: {e}")
            try:
                await db.close()
            except Exception:
                pass

    # File-based fallback
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

    db = await _get_db()
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
                content = mem.content
                await db.close()
                return content
            await db.close()
        except Exception as e:
            logger.debug(f"DB read_memory failed, falling back to files: {e}")
            try:
                await db.close()
            except Exception:
                pass

    # File-based fallback
    ensure_user_dirs(user_id)
    filepath = _memory_dir(user_id) / identifier
    if filepath.exists():
        return filepath.read_text(encoding="utf-8")
    return None


async def search_memory(user_id: str, query: str) -> list[dict[str, str]]:
    """Search memory entries by keyword. Returns matching entries."""
    db = await _get_db()
    if db:
        try:
            from core.db_models import Memory
            from sqlalchemy import select, or_
            query_pattern = f"%{query}%"
            result = await db.execute(
                select(Memory)
                .where(
                    Memory.user_id == user_id,
                    or_(
                        Memory.title.ilike(query_pattern),
                        Memory.content.ilike(query_pattern),
                    ),
                )
                .order_by(Memory.created_at)
            )
            rows = result.scalars().all()
            await db.close()
            return [{"id": m.id, "title": m.title, "content": m.content} for m in rows]
        except Exception as e:
            logger.debug(f"DB search_memory failed, falling back to files: {e}")
            try:
                await db.close()
            except Exception:
                pass

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


async def list_memories(user_id: str) -> list[dict[str, str]]:
    """List all memory entries with titles."""
    db = await _get_db()
    if db:
        try:
            from core.db_models import Memory
            from sqlalchemy import select
            result = await db.execute(
                select(Memory.id, Memory.title, Memory.memory_type, Memory.created_at)
                .where(Memory.user_id == user_id)
                .order_by(Memory.created_at)
            )
            rows = result.all()
            await db.close()
            return [{"id": row.id, "title": row.title, "type": row.memory_type} for row in rows]
        except Exception as e:
            logger.debug(f"DB list_memories failed, falling back to files: {e}")
            try:
                await db.close()
            except Exception:
                pass

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


async def delete_memory(user_id: str, identifier: str) -> bool:
    """Delete a memory entry by ID. Returns True if deleted."""
    db = await _get_db()
    if db:
        try:
            from core.db_models import Memory
            from sqlalchemy import delete
            result = await db.execute(
                delete(Memory).where(Memory.id == identifier, Memory.user_id == user_id)
            )
            await db.commit()
            deleted = result.rowcount > 0
            await db.close()
            return deleted
        except Exception as e:
            logger.debug(f"DB delete_memory failed: {e}")
            try:
                await db.close()
            except Exception:
                pass
    return False


# ── Memory tools (registered for Claude to use) ─────────────────────

def set_current_user(user_id: str) -> None:
    _current_user_id.set(user_id)


async def handle_save_memory(tool_input: dict[str, Any]) -> str:
    title = tool_input.get("title", "untitled")
    content = tool_input.get("content", "")
    if not content:
        return json.dumps({"error": "No content provided"})
    result = await save_memory(_current_user_id.get(), title, content)
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
    if not query:
        return json.dumps({"error": "No query provided"})
    results = await search_memory(_current_user_id.get(), query)
    return json.dumps({"results": results, "count": len(results)})


async def handle_list_memories(tool_input: dict[str, Any]) -> str:
    memories = await list_memories(_current_user_id.get())
    return json.dumps({"memories": memories, "count": len(memories)})


register_tool(
    name="SaveMemory",
    aliases=["save_memory"],
    description="Save information to remember for future conversations. Use this when the user shares preferences, project context, or important facts.",
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
    description="Search saved memories by keyword.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search term",
            },
        },
        "required": ["query"],
    },
    handler=handle_search_memory,
)

register_tool(
    name="ListMemories",
    aliases=["list_memories"],
    description="List all saved memory entries.",
    input_schema={
        "type": "object",
        "properties": {},
    },
    handler=handle_list_memories,
)
