"""Per-user file-based memory system — same pattern as Claude Code."""

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

# Default data root — overridden by config
_DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "users"

# Context-var for async-safe per-request user identity (replaces global)
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

    index = _memory_index(user_id)
    if not index.exists():
        index.write_text(f"# Memory for {user_id}\n", encoding="utf-8")


def load_memory(user_id: str) -> str:
    """Load memory index for injection into system prompt.

    Returns a listing of available memory files with titles so the model
    knows what's available. Individual files can be read on demand via
    the ReadMemory tool (Zettelkasten pattern).
    """
    ensure_user_dirs(user_id)
    mem_dir = _memory_dir(user_id)
    files = sorted(mem_dir.glob("*.md"))
    if not files:
        return ""
    lines: list[str] = []
    for f in files:
        # Extract first heading or use filename as title
        text = f.read_text(encoding="utf-8").strip()
        title = f.stem
        for line in text.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                title = stripped
                break
        lines.append(f"- **{title}** (`{f.name}`)")
    return "Available memories (use ReadMemory to view full content):\n" + "\n".join(lines)


def save_memory(user_id: str, title: str, content: str) -> str:
    """Save a memory entry. Returns the file path."""
    ensure_user_dirs(user_id)
    today = date.today().isoformat()
    slug = re.sub(r"[^\w\-]", "", title.lower().replace(" ", "-"))[:50]
    filename = f"{today}-{slug}.md"
    filepath = _memory_dir(user_id) / filename

    filepath.write_text(content, encoding="utf-8")

    # Append to index (with file lock for concurrent safety)
    index = _memory_index(user_id)
    lock = FileLock(str(index) + ".lock")
    entry = f"\n- [{title}](memory/{filename})\n"
    with lock:
        with open(index, "a", encoding="utf-8") as f:
            f.write(entry)

    return str(filepath)


def read_memory(user_id: str, filename: str) -> str | None:
    """Read a single memory file by filename. Returns content or None."""
    ensure_user_dirs(user_id)
    filepath = _memory_dir(user_id) / filename
    # Prevent path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        return None
    if not filepath.exists():
        return None
    return filepath.read_text(encoding="utf-8")


def search_memory(user_id: str, query: str) -> list[dict[str, str]]:
    """Search memory files by keyword. Returns matching files with full content."""
    ensure_user_dirs(user_id)
    query_lower = query.lower()
    results: list[dict[str, str]] = []

    for md_file in sorted(_memory_dir(user_id).glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        if query_lower in text.lower():
            results.append({"file": md_file.name, "content": text})

    return results


def list_memories(user_id: str) -> list[dict[str, str]]:
    """List all memory files with titles."""
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


# --- Memory tools (registered for Claude to use) ---
# User identity is carried via a ContextVar — async-safe across concurrent requests.


def set_current_user(user_id: str) -> None:
    _current_user_id.set(user_id)


async def handle_save_memory(tool_input: dict[str, Any]) -> str:
    title = tool_input.get("title", "untitled")
    content = tool_input.get("content", "")
    if not content:
        return json.dumps({"error": "No content provided"})
    path = save_memory(_current_user_id.get(), title, content)
    return json.dumps({"status": "saved", "path": path})


async def handle_read_memory(tool_input: dict[str, Any]) -> str:
    filename = tool_input.get("filename", "")
    if not filename:
        return json.dumps({"error": "No filename provided"})
    content = read_memory(_current_user_id.get(), filename)
    if content is None:
        return json.dumps({"error": f"Memory file not found: {filename}"})
    return json.dumps({"file": filename, "content": content})


async def handle_search_memory(tool_input: dict[str, Any]) -> str:
    query = tool_input.get("query", "")
    if not query:
        return json.dumps({"error": "No query provided"})
    results = search_memory(_current_user_id.get(), query)
    return json.dumps({"results": results, "count": len(results)})


async def handle_list_memories(tool_input: dict[str, Any]) -> str:
    memories = list_memories(_current_user_id.get())
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
    description="Read the full content of a specific memory file. Use after ListMemories or SearchMemory to view a particular note.",
    input_schema={
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "The memory filename (e.g., '2026-03-29-sofr-yield-curve-imp.md')",
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
