"""Glob tool — find files by pattern."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.registry import register_tool


async def handle_glob(tool_input: dict[str, Any]) -> str:
    pattern = tool_input.get("pattern", "")
    search_path = tool_input.get("path", ".")

    if not pattern:
        return json.dumps({"error": "No pattern provided"})

    from core.sandbox import validate_tool_path
    root, err = validate_tool_path(search_path, read_only=True)
    if err:
        return err

    if not root.exists():
        return json.dumps({"error": f"Path not found: {search_path}"})

    try:
        files = sorted(
            [str(p) for p in root.glob(pattern) if p.is_file()],
            key=lambda p: Path(p).stat().st_mtime,
            reverse=True,
        )
        return json.dumps({"files": files[:200], "total": len(files)})
    except Exception as e:
        return json.dumps({"error": str(e)})


register_tool(
    name="Glob",
    aliases=["glob"],
    description=(
        "Find files matching a glob pattern. Returns file paths sorted by modification time "
        "(most recent first). Use this instead of bash find/ls for file discovery. "
        "Supports standard glob patterns: '**/*.py' (recursive), 'src/*.ts' (single dir), "
        "'*.{js,ts}' (multiple extensions). Returns up to 200 results. "
        "Use Grep instead if you need to search file contents, not just file names."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern (e.g. '**/*.py', 'src/**/*.ts', '*.{js,ts}')",
            },
            "path": {
                "type": "string",
                "description": "Directory to search in. Defaults to project root.",
            },
        },
        "required": ["pattern"],
    },
    handler=handle_glob,
)
