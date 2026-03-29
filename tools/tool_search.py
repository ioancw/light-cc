"""ToolSearch tool — search MCP and built-in tool names/descriptions by keyword."""

from __future__ import annotations

import json
from typing import Any

from tools.registry import register_tool


async def handle_tool_search(tool_input: dict[str, Any]) -> str:
    query = tool_input.get("query", "")
    max_results = tool_input.get("max_results", 5)

    if not query:
        return json.dumps({"error": "No query provided"})

    from core.mcp_client import get_mcp_manager
    manager = get_mcp_manager()
    results = manager.search_tools(query, max_results=max_results)

    # Also search built-in tools
    from tools.registry import get_all_tool_schemas
    query_lower = query.lower()
    keywords = query_lower.split()
    for schema in get_all_tool_schemas():
        name = schema.get("name", "")
        # Skip MCP tools (already searched) and ToolSearch itself
        if "__" in name or name == "ToolSearch":
            continue
        name_lower = name.lower()
        desc_lower = schema.get("description", "").lower()
        score = sum(2 for kw in keywords if kw in name_lower) + sum(1 for kw in keywords if kw in desc_lower)
        if score > 0:
            results.append(schema)

    # Deduplicate and limit
    seen = set()
    unique = []
    for r in results:
        name = r.get("name", "")
        if name not in seen:
            seen.add(name)
            unique.append(r)
    unique = unique[:max_results]

    return json.dumps({
        "matches": [
            {"name": r["name"], "description": r.get("description", ""), "input_schema": r.get("input_schema", {})}
            for r in unique
        ],
        "count": len(unique),
    })


register_tool(
    name="ToolSearch",
    aliases=["tool_search"],
    description="Search available tools by keyword. Returns matching tool names, descriptions, and schemas.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keywords to search for in tool names and descriptions",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (default 5)",
            },
        },
        "required": ["query"],
    },
    handler=handle_tool_search,
)
