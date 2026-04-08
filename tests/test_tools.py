"""Tests for tool registry and execution (tools/registry.py, sandbox_exec)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from tools.registry import (
    _TOOLS,
    _ALIASES,
    execute_tool,
    get_all_tool_schemas,
    get_tool_schemas,
    list_tool_names,
    register_tool,
    resolve_tool_name,
)


class TestToolRegistration:
    def test_register_and_resolve(self, clean_tool_registry):
        async def handler(inp):
            return "ok"

        register_tool("MyTool", "A test tool", {"type": "object"}, handler, aliases=["my_tool", "mytool"])

        assert "MyTool" in _TOOLS
        assert resolve_tool_name("my_tool") == "MyTool"
        assert resolve_tool_name("mytool") == "MyTool"
        assert resolve_tool_name("MyTool") == "MyTool"

    def test_resolve_unknown_returns_self(self):
        assert resolve_tool_name("UnknownTool") == "UnknownTool"

    def test_list_tool_names(self, clean_tool_registry):
        async def handler(inp):
            return "ok"

        register_tool("Alpha", "desc", {}, handler)
        register_tool("Beta", "desc", {}, handler)

        names = list_tool_names()
        assert "Alpha" in names
        assert "Beta" in names


class TestToolSchemas:
    def test_get_all_schemas(self, clean_tool_registry):
        async def handler(inp):
            return "ok"

        register_tool("TestTool", "A tool", {"type": "object", "properties": {}}, handler)

        with patch("core.mcp_client.get_mcp_manager") as mock_mcp:
            mock_mcp.return_value.get_all_tool_schemas.return_value = []
            schemas = get_all_tool_schemas()

        found = [s for s in schemas if s["name"] == "TestTool"]
        assert len(found) == 1
        assert found[0]["description"] == "A tool"

    def test_get_tool_schemas_subset(self, clean_tool_registry):
        async def handler(inp):
            return "ok"

        register_tool("ToolA", "A", {}, handler)
        register_tool("ToolB", "B", {}, handler)
        register_tool("ToolC", "C", {}, handler)

        schemas = get_tool_schemas(["ToolA", "ToolC"])
        names = [s["name"] for s in schemas]
        assert names == ["ToolA", "ToolC"]

    def test_get_tool_schemas_with_alias(self, clean_tool_registry):
        async def handler(inp):
            return "ok"

        register_tool("Bash", "Shell", {}, handler, aliases=["bash", "shell"])

        schemas = get_tool_schemas(["bash"])
        assert len(schemas) == 1
        assert schemas[0]["name"] == "Bash"

    def test_get_tool_schemas_deduplicates(self, clean_tool_registry):
        async def handler(inp):
            return "ok"

        register_tool("Bash", "Shell", {}, handler, aliases=["bash"])

        schemas = get_tool_schemas(["Bash", "bash"])
        assert len(schemas) == 1


class TestToolExecution:
    @pytest.mark.asyncio
    async def test_execute_builtin_tool(self, clean_tool_registry):
        async def handler(inp):
            return json.dumps({"result": inp["value"]})

        register_tool("Echo", "Echo back", {"type": "object"}, handler)

        result = await execute_tool("Echo", {"value": "hello"})
        parsed = json.loads(result)
        assert parsed["result"] == "hello"

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        result = await execute_tool("NonexistentTool99", {})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "Unknown tool" in parsed["error"]

    @pytest.mark.asyncio
    async def test_execute_tool_exception_returns_error(self, clean_tool_registry):
        async def bad_handler(inp):
            raise ValueError("Something broke")

        register_tool("Broken", "Breaks", {}, bad_handler)

        result = await execute_tool("Broken", {})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "Something broke" in parsed["error"]

    @pytest.mark.asyncio
    async def test_result_truncation(self, clean_tool_registry):
        async def big_handler(inp):
            return "x" * 100_000

        register_tool("BigOutput", "Returns huge result", {}, big_handler)

        with patch("core.config.settings") as mock_settings:
            mock_settings.max_tool_result_chars = 1000
            result = await execute_tool("BigOutput", {})

        assert len(result) <= 1100  # 1000 + truncation message
        assert "TRUNCATED" in result

    @pytest.mark.asyncio
    async def test_mcp_tool_routing(self, clean_tool_registry):
        """Tools with server__name format should route to MCP manager."""
        mock_manager = MagicMock()
        mock_manager.parse_namespaced_tool.return_value = ("my_server", "my_tool")
        mock_manager.call_tool = AsyncMock(return_value="mcp result")

        with patch("core.mcp_client.get_mcp_manager", return_value=mock_manager):
            result = await execute_tool("my_server__my_tool", {"arg": "val"})

        assert result == "mcp result"
        mock_manager.call_tool.assert_called_once_with("my_server", "my_tool", {"arg": "val"})

    @pytest.mark.asyncio
    async def test_alias_execution(self, clean_tool_registry):
        async def handler(inp):
            return "alias_works"

        register_tool("Bash", "Shell", {}, handler, aliases=["bash"])

        result = await execute_tool("bash", {})
        assert result == "alias_works"


class TestSandboxExec:
    def test_safe_env_excludes_secrets(self):
        from core.sandbox_exec import _build_safe_env
        import os

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-secret", "PATH": "/usr/bin"}):
            env = _build_safe_env(output_dir="/tmp/outputs")

        assert "ANTHROPIC_API_KEY" not in env
        assert "PATH" in env

    def test_safe_env_includes_output_dir(self):
        from core.sandbox_exec import _build_safe_env

        env = _build_safe_env(output_dir="/my/outputs")
        assert env.get("OUTPUT_DIR") == "/my/outputs"

    def test_safe_env_excludes_database_url(self):
        from core.sandbox_exec import _build_safe_env
        import os

        with patch.dict(os.environ, {"DATABASE_URL": "postgres://secret", "PATH": "/usr/bin"}):
            env = _build_safe_env(output_dir="/tmp/outputs")

        assert "DATABASE_URL" not in env

    def test_safe_env_excludes_jwt_secret(self):
        from core.sandbox_exec import _build_safe_env
        import os

        with patch.dict(os.environ, {"JWT_SECRET": "my-secret", "PATH": "/usr/bin"}):
            env = _build_safe_env(output_dir="/tmp/outputs")

        assert "JWT_SECRET" not in env
