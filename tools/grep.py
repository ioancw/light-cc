"""Grep tool — regex search across files."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tools.registry import register_tool


async def handle_grep(tool_input: dict[str, Any]) -> str:
    pattern = tool_input.get("pattern", "")
    search_path = tool_input.get("path", ".")
    glob_filter = tool_input.get("glob", "**/*")
    max_results = tool_input.get("max_results", 50)

    if not pattern:
        return json.dumps({"error": "No pattern provided"})

    try:
        regex = re.compile(pattern, re.IGNORECASE if tool_input.get("ignore_case") else 0)
    except re.error as e:
        return json.dumps({"error": f"Invalid regex: {e}"})

    from core.sandbox import validate_tool_path
    root, err = validate_tool_path(search_path, read_only=True)
    if err:
        return err

    if not root.exists():
        return json.dumps({"error": f"Path not found: {search_path}"})

    matches: list[dict[str, Any]] = []
    try:
        for file_path in root.glob(glob_filter):
            if not file_path.is_file():
                continue
            if len(matches) >= max_results:
                break
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        matches.append(
                            {
                                "file": str(file_path),
                                "line": i,
                                "content": line.strip()[:200],
                            }
                        )
                        if len(matches) >= max_results:
                            break
            except (PermissionError, OSError):
                continue

        return json.dumps({"matches": matches, "count": len(matches)})
    except Exception as e:
        return json.dumps({"error": str(e)})


register_tool(
    name="Grep",
    aliases=["grep"],
    description="Search for a regex pattern across files. Returns matching lines with file paths and line numbers.",
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for",
            },
            "path": {
                "type": "string",
                "description": "Directory or file to search in (default: current dir)",
            },
            "glob": {
                "type": "string",
                "description": "Glob pattern to filter files (default: **/*)",
            },
            "ignore_case": {
                "type": "boolean",
                "description": "Case insensitive search (default false)",
            },
            "max_results": {
                "type": "integer",
                "description": "Max results to return (default 50)",
            },
        },
        "required": ["pattern"],
    },
    handler=handle_grep,
)
