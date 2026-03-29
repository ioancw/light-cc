"""Edit tool — search and replace within a file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.registry import register_tool


async def handle_edit(tool_input: dict[str, Any]) -> str:
    file_path = tool_input.get("file_path", "")
    old_string = tool_input.get("old_string", "")
    new_string = tool_input.get("new_string", "")

    if not file_path:
        return json.dumps({"error": "No file_path provided"})
    if not old_string:
        return json.dumps({"error": "No old_string provided"})

    from core.sandbox import validate_tool_path
    path, err = validate_tool_path(file_path)
    if err:
        return err

    if not path.exists():
        return json.dumps({"error": f"File not found: {file_path}"})

    try:
        # Checkpoint before editing
        from core.checkpoints import snapshot_file
        from core.session import _current_session_id
        sid = _current_session_id.get("")
        if sid:
            snapshot_file(sid, str(path))

        text = path.read_text(encoding="utf-8")
        replace_all = tool_input.get("replace_all", False)

        if old_string not in text:
            return json.dumps({"error": "old_string not found in file"})

        if replace_all:
            count = text.count(old_string)
            new_text = text.replace(old_string, new_string)
        else:
            count = 1
            new_text = text.replace(old_string, new_string, 1)

        path.write_text(new_text, encoding="utf-8")
        return json.dumps({"status": "ok", "replacements": count})
    except Exception as e:
        return json.dumps({"error": str(e)})


register_tool(
    name="Edit",
    aliases=["edit"],
    description="Edit a file by replacing old_string with new_string.",
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file",
            },
            "old_string": {
                "type": "string",
                "description": "The exact text to find and replace",
            },
            "new_string": {
                "type": "string",
                "description": "The replacement text",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences (default false)",
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    },
    handler=handle_edit,
)
