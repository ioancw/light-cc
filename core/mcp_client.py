"""MCP client manager — connect to MCP servers, discover tools, proxy calls.

Supports stdio (subprocess) and HTTP (remote) transports via the official
MCP Python SDK.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)

# Separator between server name and tool name in namespaced tool IDs
_NS_SEP = "__"


def _stdio_allowlist() -> set[str]:
    """Admin-controlled allowlist of MCP stdio server names permitted to spawn.

    Env var `MCP_STDIO_ALLOWLIST` is a comma-separated list. Empty/unset means
    no stdio servers may spawn — only HTTP MCP servers are allowed. This exists
    because stdio spawns an arbitrary subprocess inside the app container.
    """
    raw = os.environ.get("MCP_STDIO_ALLOWLIST", "").strip()
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


class MCPStdioNotAllowed(RuntimeError):
    """Raised when an MCP stdio spawn is requested for a non-allowlisted server."""


class MCPServerConnection:
    """Holds a live connection to a single MCP server."""

    def __init__(self, name: str, session: ClientSession, exit_stack: AsyncExitStack):
        self.name = name
        self.session = session
        self._exit_stack = exit_stack
        self.tools: list[dict[str, Any]] = []  # Claude API-format schemas

    async def discover_tools(self) -> list[dict[str, Any]]:
        """Call tools/list and cache results as Claude API tool schemas."""
        result = await self.session.list_tools()
        self.tools = []
        for tool in result.tools:
            schema = {
                "name": f"{self.name}{_NS_SEP}{tool.name}",
                "description": f"[{self.name}] {tool.description or ''}".strip(),
                "input_schema": tool.inputSchema,
            }
            self.tools.append(schema)
        logger.info(f"MCP server '{self.name}': discovered {len(self.tools)} tools")
        return self.tools

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on this server and return the result as a string."""
        result = await self.session.call_tool(tool_name, arguments)
        # Combine all content blocks into a single string
        parts = []
        for content in result.content:
            if hasattr(content, "text"):
                parts.append(content.text)
            elif hasattr(content, "data"):
                parts.append(f"[binary data: {content.mimeType}]")
            else:
                parts.append(str(content))
        return "\n".join(parts) if parts else json.dumps({"status": "ok"})


class MCPManager:
    """Manages connections to multiple MCP servers."""

    def __init__(self):
        self._servers: dict[str, MCPServerConnection] = {}

    async def connect_stdio(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> MCPServerConnection:
        """Start an MCP server as a subprocess and connect via stdio.

        Gated on the admin-set `MCP_STDIO_ALLOWLIST` env var to stop a
        plugin drop-in from silently spawning arbitrary local processes.
        """
        allowed = _stdio_allowlist()
        if name not in allowed:
            raise MCPStdioNotAllowed(
                f"MCP stdio server '{name}' not in MCP_STDIO_ALLOWLIST. "
                "Add it to the env var on the host and restart to enable."
            )

        if name in self._servers:
            logger.warning(f"MCP server '{name}' already connected, disconnecting first")
            await self.disconnect(name)

        exit_stack = AsyncExitStack()
        try:
            server_params = StdioServerParameters(
                command=command,
                args=args or [],
                env=env,
            )
            read_stream, write_stream = await exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            session = await exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()

            conn = MCPServerConnection(name, session, exit_stack)
            await conn.discover_tools()
            self._servers[name] = conn
            logger.info(f"MCP stdio server '{name}' connected: {command} {args or []}")
            return conn
        except Exception:
            await exit_stack.aclose()
            raise

    async def connect_http(
        self,
        name: str,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> MCPServerConnection:
        """Connect to a remote MCP server via HTTP."""
        if name in self._servers:
            logger.warning(f"MCP server '{name}' already connected, disconnecting first")
            await self.disconnect(name)

        exit_stack = AsyncExitStack()
        try:
            read_stream, write_stream, _ = await exit_stack.enter_async_context(
                streamablehttp_client(url, headers=headers)
            )
            session = await exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()

            conn = MCPServerConnection(name, session, exit_stack)
            await conn.discover_tools()
            self._servers[name] = conn
            logger.info(f"MCP HTTP server '{name}' connected: {url}")
            return conn
        except Exception:
            await exit_stack.aclose()
            raise

    def get_all_tool_schemas(self) -> list[dict[str, Any]]:
        """Get all discovered MCP tool schemas across all servers."""
        schemas = []
        for conn in self._servers.values():
            schemas.extend(conn.tools)
        return schemas

    def search_tools(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        """Search MCP tool names/descriptions by keyword. Returns matching schemas."""
        query_lower = query.lower()
        keywords = query_lower.split()
        scored: list[tuple[int, dict[str, Any]]] = []
        for schema in self.get_all_tool_schemas():
            name = schema.get("name", "").lower()
            desc = schema.get("description", "").lower()
            score = sum(2 for kw in keywords if kw in name) + sum(1 for kw in keywords if kw in desc)
            if score > 0:
                scored.append((score, schema))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:max_results]]

    def parse_namespaced_tool(self, namespaced_name: str) -> tuple[str, str] | None:
        """Parse 'server__tool' into (server_name, tool_name), or None."""
        if _NS_SEP not in namespaced_name:
            return None
        server_name, tool_name = namespaced_name.split(_NS_SEP, 1)
        if server_name in self._servers:
            return server_name, tool_name
        return None

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on a specific MCP server."""
        conn = self._servers.get(server_name)
        if not conn:
            return json.dumps({"error": f"MCP server '{server_name}' not connected"})
        try:
            return await conn.call_tool(tool_name, arguments)
        except Exception as e:
            return json.dumps({"error": f"MCP tool call failed: {e}"})

    async def disconnect(self, name: str) -> None:
        """Disconnect a specific MCP server."""
        conn = self._servers.pop(name, None)
        if conn:
            await conn._exit_stack.aclose()
            logger.info(f"MCP server '{name}' disconnected")

    async def disconnect_all(self) -> None:
        """Disconnect all MCP servers."""
        names = list(self._servers.keys())
        for name in names:
            await self.disconnect(name)

    @property
    def server_names(self) -> list[str]:
        return list(self._servers.keys())


# Singleton instance
_manager: MCPManager | None = None


def get_mcp_manager() -> MCPManager:
    """Get or create the global MCPManager instance."""
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager


async def load_mcp_config(config_path: str) -> MCPManager:
    """Load .mcp.json and connect to all configured servers.

    Format:
    {
        "mcpServers": {
            "server-name": {
                "command": "python",
                "args": ["-m", "my_server"],
                "env": {"KEY": "value"}
            },
            "remote-server": {
                "type": "http",
                "url": "https://api.example.com/mcp",
                "headers": {"Authorization": "Bearer ..."}
            }
        }
    }
    """
    import json as json_mod
    from pathlib import Path

    path = Path(config_path)
    if not path.exists():
        logger.debug(f"No MCP config at {config_path}")
        return get_mcp_manager()

    try:
        config = json_mod.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Failed to parse {config_path}: {e}")
        return get_mcp_manager()

    manager = get_mcp_manager()
    servers = config.get("mcpServers", {})

    for name, server_config in servers.items():
        try:
            if server_config.get("type") == "http" or "url" in server_config:
                await manager.connect_http(
                    name=name,
                    url=server_config["url"],
                    headers=server_config.get("headers"),
                )
            else:
                await manager.connect_stdio(
                    name=name,
                    command=server_config["command"],
                    args=server_config.get("args", []),
                    env=server_config.get("env"),
                )
        except MCPStdioNotAllowed as e:
            logger.warning(f"Skipping MCP server '{name}': {e}")
        except Exception as e:
            logger.error(f"Failed to connect MCP server '{name}': {e}")

    return manager
