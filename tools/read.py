"""Read tool — read file contents with optional line range."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.registry import register_tool


async def handle_read(tool_input: dict[str, Any]) -> str:
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return json.dumps({"error": "No file_path provided"})

    from core.sandbox import validate_tool_path
    path, err = validate_tool_path(file_path, read_only=True)
    if err:
        return err

    if not path.exists():
        return json.dumps({"error": f"File not found: {file_path}"})
    if not path.is_file():
        return json.dumps({"error": f"Not a file: {file_path}"})

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()

        offset = tool_input.get("offset", 0)
        limit = tool_input.get("limit", 2000)
        selected = lines[offset : offset + limit]

        numbered = "\n".join(
            f"{i + offset + 1:>6}\t{line}" for i, line in enumerate(selected)
        )

        result = {
            "content": numbered,
            "total_lines": len(lines),
            "showing": f"{offset + 1}-{offset + len(selected)}",
        }
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


register_tool(
    name="Read",
    aliases=["read"],
    description=(
        "Read a file's contents. Returns line-numbered text (cat -n format). "
        "Use this instead of bash cat/head/tail. "
        "For large files, use offset and limit to read specific sections — "
        "e.g. offset=100, limit=50 reads lines 100-149. "
        "Default reads up to 2000 lines from the start. "
        "Returns an error if the path is a directory — use bash ls for directories."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to read (e.g. C:/Users/me/project/src/main.py)",
            },
            "offset": {
                "type": "integer",
                "description": "Line number to start from (0-based). Use with limit to read a slice of a large file.",
            },
            "limit": {
                "type": "integer",
                "description": "Max lines to read (default 2000). Reduce for large files when you only need a section.",
            },
        },
        "required": ["file_path"],
    },
    handler=handle_read,
)
