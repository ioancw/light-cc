"""Admin gate + install safety tests for the plugin subsystem.

Covers S3 of the security plan:
- `/plugin install|update|uninstall` refuses non-admin callers
- `/plugin list` stays open to all users
- `install_from_path` rejects trees containing symlinks
- MCP stdio spawn refuses server names that aren't on the admin allowlist
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


# Ensure a minimal manifest on disk for path-based tests
def _write_manifest(dir_: Path, name: str = "probe-plugin") -> None:
    meta = dir_ / ".claude-plugin"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "plugin.json").write_text(
        '{"name": "' + name + '", "version": "0.1.0", "description": "t"}',
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_non_admin_cannot_install():
    from handlers.commands import handle_plugin_command
    out = await handle_plugin_command("install some/repo", user_is_admin=False)
    assert "permission denied" in out.lower()


@pytest.mark.asyncio
async def test_non_admin_cannot_update():
    from handlers.commands import handle_plugin_command
    out = await handle_plugin_command("update something", user_is_admin=False)
    assert "permission denied" in out.lower()


@pytest.mark.asyncio
async def test_non_admin_cannot_uninstall():
    from handlers.commands import handle_plugin_command
    out = await handle_plugin_command("uninstall something", user_is_admin=False)
    assert "permission denied" in out.lower()


@pytest.mark.asyncio
async def test_non_admin_can_list():
    from handlers.commands import handle_plugin_command
    out = await handle_plugin_command("list", user_is_admin=False)
    # Must not be the permission-denied message; list is open.
    assert "permission denied" not in out.lower()


@pytest.mark.asyncio
async def test_symlink_in_source_is_rejected(tmp_path):
    if sys.platform.startswith("win"):
        pytest.skip("symlink creation on Windows requires elevation")

    import core.plugin_manager as pm

    src = tmp_path / "plugin_src"
    src.mkdir()
    _write_manifest(src)
    # Create a dangling symlink inside the plugin tree.
    (src / "evil_link").symlink_to("/etc/shadow")

    # Point plugins_dir at tmp_path so the test can't touch the real tree.
    dest_root = tmp_path / "installed"
    dest_root.mkdir()

    def _stub_plugins_dir():
        return dest_root

    original = pm._resolve_plugins_dir
    pm._resolve_plugins_dir = _stub_plugins_dir
    try:
        with pytest.raises(pm.PluginError, match="symlink"):
            await pm.install_from_path(src)
    finally:
        pm._resolve_plugins_dir = original


@pytest.mark.asyncio
async def test_stdio_spawn_refused_without_allowlist(monkeypatch):
    monkeypatch.delenv("MCP_STDIO_ALLOWLIST", raising=False)
    from core.mcp_client import MCPManager, MCPStdioNotAllowed

    manager = MCPManager()
    with pytest.raises(MCPStdioNotAllowed):
        await manager.connect_stdio(name="evil-server", command="/bin/sh", args=["-c", "echo"])


@pytest.mark.asyncio
async def test_stdio_spawn_refused_if_name_not_on_allowlist(monkeypatch):
    monkeypatch.setenv("MCP_STDIO_ALLOWLIST", "blessed-server")
    from core.mcp_client import MCPManager, MCPStdioNotAllowed

    manager = MCPManager()
    with pytest.raises(MCPStdioNotAllowed):
        await manager.connect_stdio(name="evil-server", command="/bin/sh", args=["-c", "echo"])
