"""Write tool — write content to a file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.registry import register_tool


async def handle_write(tool_input: dict[str, Any]) -> str:
    file_path = tool_input.get("file_path", "")
    content = tool_input.get("content", "")
    if not file_path:
        return json.dumps({"error": "No file_path provided"})

    from core.sandbox import validate_tool_path
    path, err = validate_tool_path(file_path)
    if err:
        return err

    try:
        # Checkpoint before writing (keyed by cid for multiplexed conversations)
        from core.checkpoints import snapshot_file
        from core.session import _current_session_id, _current_cid, current_session_get
        cp_key = _current_cid.get("") or _current_session_id.get("")
        if cp_key:
            snapshot_file(cp_key, str(path))

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return json.dumps({"status": "ok", "path": str(path), "bytes": len(content)})
    except Exception as e:
        return json.dumps({"error": str(e)})


register_tool(
    name="Write",
    aliases=["write"],
    description=(
        "Write content to a file, creating it and parent directories if they don't exist. "
        "WARNING: This overwrites the entire file. For partial modifications, use Edit instead. "
        "Use this for: creating new files, or complete rewrites where Edit would be impractical."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to create or overwrite",
            },
            "content": {
                "type": "string",
                "description": "The full content to write to the file. This replaces the entire file.",
            },
        },
        "required": ["file_path", "content"],
    },
    handler=handle_write,
)
