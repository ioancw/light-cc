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
        # Checkpoint before editing (keyed by cid for multiplexed conversations)
        from core.checkpoints import snapshot_file
        from core.session import _current_session_id, _current_cid
        cp_key = _current_cid.get("") or _current_session_id.get("")
        if cp_key:
            snapshot_file(cp_key, str(path))

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
    description=(
        "Edit a file by replacing an exact string match with new text. "
        "The old_string must appear exactly once in the file (including whitespace and indentation) "
        "or the edit will fail — include enough surrounding context to make the match unique. "
        "To replace all occurrences, set replace_all=true. "
        "Prefer this over Write for modifications — it only changes what you specify. "
        "Use Write only for creating new files or complete rewrites."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to edit",
            },
            "old_string": {
                "type": "string",
                "description": (
                    "The exact text to find and replace. Must match the file content exactly, "
                    "including indentation (spaces/tabs). Include enough surrounding context "
                    "to make the match unique."
                ),
            },
            "new_string": {
                "type": "string",
                "description": "The replacement text. Must differ from old_string.",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences instead of requiring a unique match (default false)",
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    },
    handler=handle_edit,
)
