"""Structured tool artifacts -- typed output from tool execution.

Tools can return artifacts (files, charts, images, tables) as structured
metadata instead of relying on stdout path scanning. The media handlers
check for artifacts first, then fall back to stdout scanning for
backward compatibility.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Artifact:
    """A file or rich content produced by a tool."""
    type: str          # "image", "chart", "table", "html", "csv", "file"
    path: str          # Absolute file path
    mime: str = ""     # MIME type (e.g., "image/png")
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """Structured result from a tool execution."""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    artifacts: list[Artifact] = field(default_factory=list)

    def to_json(self) -> str:
        d: dict[str, Any] = {"stdout": self.stdout}
        if self.stderr:
            d["stderr"] = self.stderr
        if self.exit_code != 0:
            d["exit_code"] = self.exit_code
        if self.artifacts:
            d["artifacts"] = [
                {"type": a.type, "path": a.path, "mime": a.mime, **a.metadata}
                for a in self.artifacts
            ]
        return json.dumps(d)


def parse_tool_result(raw: str) -> ToolResult:
    """Parse a tool result string into a ToolResult.

    Handles both the new structured format (with "artifacts" key) and the
    legacy format (plain JSON with stdout/stderr).
    """
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ToolResult(stdout=raw)

    if not isinstance(parsed, dict):
        return ToolResult(stdout=raw)

    # Check for error result
    if "error" in parsed:
        return ToolResult(stderr=parsed["error"], exit_code=1)

    artifacts = []
    for a in parsed.get("artifacts", []):
        if isinstance(a, dict) and "type" in a and "path" in a:
            meta = {k: v for k, v in a.items() if k not in ("type", "path", "mime")}
            artifacts.append(Artifact(
                type=a["type"],
                path=a["path"],
                mime=a.get("mime", ""),
                metadata=meta,
            ))

    return ToolResult(
        stdout=parsed.get("stdout", ""),
        stderr=parsed.get("stderr", ""),
        exit_code=parsed.get("exit_code", 0),
        artifacts=artifacts,
    )
