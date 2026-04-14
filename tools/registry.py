"""Tool registry — maps tool names to handlers and Claude API schemas."""

from __future__ import annotations

import json
from typing import Any, Callable, Awaitable

# Registry: name -> (async_handler, claude_tool_schema)
_TOOLS: dict[str, tuple[Callable[..., Awaitable[str]], dict[str, Any]]] = {}

# Aliases: alternate_name -> canonical_name (e.g. "bash" -> "Bash")
_ALIASES: dict[str, str] = {}


def register_tool(
    name: str,
    description: str,
    input_schema: dict[str, Any],
    handler: Callable[..., Awaitable[str]],
    aliases: list[str] | None = None,
) -> None:
    """Register a tool with its handler and Claude API schema.

    The canonical name should be PascalCase (matching Claude Code).
    Aliases (e.g. snake_case) are resolved transparently.
    """
    schema = {
        "name": name,
        "description": description,
        "input_schema": input_schema,
    }
    _TOOLS[name] = (handler, schema)
    for alias in aliases or []:
        _ALIASES[alias] = name


def resolve_tool_name(name: str) -> str:
    """Resolve an alias to its canonical tool name."""
    return _ALIASES.get(name, name)


def get_all_tool_schemas() -> list[dict[str, Any]]:
    """Get all tool schemas for the Claude API tools parameter.

    Includes both built-in tools and MCP-discovered tools.
    """
    schemas = [schema for _, schema in _TOOLS.values()]
    try:
        from core.mcp_client import get_mcp_manager
        schemas.extend(get_mcp_manager().get_all_tool_schemas())
    except Exception:
        pass
    return schemas


def get_tool_schemas(names: list[str]) -> list[dict[str, Any]]:
    """Get tool schemas for a subset of tools by name.

    Accepts both canonical names and aliases.
    """
    schemas = []
    seen = set()
    for n in names:
        resolved = resolve_tool_name(n)
        if resolved in _TOOLS and resolved not in seen:
            schemas.append(_TOOLS[resolved][1])
            seen.add(resolved)
    return schemas


async def execute_tool(name: str, tool_input: dict[str, Any]) -> str:
    """Execute a tool by name. Returns result string, truncated if too large.

    Routes MCP tools (server__tool format) to the MCP manager,
    and built-in tools to their registered handlers.
    """
    from core.config import settings

    # Check if this is an MCP-namespaced tool (server__tool)
    try:
        from core.mcp_client import get_mcp_manager
        manager = get_mcp_manager()
        parsed = manager.parse_namespaced_tool(name)
        if parsed:
            server_name, tool_name = parsed
            result = await manager.call_tool(server_name, tool_name, tool_input)
            limit = settings.max_tool_result_chars
            if len(result) > limit:
                result = result[:limit] + "\n\n[TRUNCATED — result exceeded limit]"
            return result
    except Exception:
        pass

    # Built-in tool
    resolved = resolve_tool_name(name)
    if resolved not in _TOOLS:
        return json.dumps({"error": f"Unknown tool: {name}"})
    handler, _ = _TOOLS[resolved]
    try:
        result = await handler(tool_input)
    except Exception as e:
        return json.dumps({"error": str(e)})

    # Truncate large results to prevent context blowup
    limit = settings.max_tool_result_chars
    if len(result) > limit:
        result = result[:limit] + "\n\n[TRUNCATED — result exceeded limit]"
    return result


def list_tool_names() -> list[str]:
    """List all registered tool names (canonical only)."""
    return list(_TOOLS.keys())


def get_tool_description(name: str) -> str:
    """Return the registered description for a tool, or '' if unknown.

    Resolves aliases and falls back to MCP-discovered tools if the name isn't
    built-in. Used by the chat UI to show what a tool does in expanded tool
    cards without hard-coding descriptions client-side.
    """
    resolved = resolve_tool_name(name)
    if resolved in _TOOLS:
        return _TOOLS[resolved][1].get("description", "")
    try:
        from core.mcp_client import get_mcp_manager
        for s in get_mcp_manager().get_all_tool_schemas():
            if s.get("name") == name:
                return s.get("description", "")
    except Exception:
        pass
    return ""
