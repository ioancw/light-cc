#!/usr/bin/env python3
"""Plugin management CLI for Light CC.

A thin wrapper over core/plugin_manager.py — same logic the REST API uses.

Usage:
    python scripts/plugin_cli.py install <url-or-path>
    python scripts/plugin_cli.py list
    python scripts/plugin_cli.py update <name>
    python scripts/plugin_cli.py uninstall <name>
"""

from __future__ import annotations

import asyncio
import sys

from core import plugin_manager


def cmd_install(source: str) -> None:
    try:
        manifest = asyncio.run(plugin_manager.install(source))
    except plugin_manager.PluginError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    print(f"Installed '{manifest['name']}' v{manifest.get('version', '?')}")
    print("Restart Light CC to activate the plugin.")


def cmd_list() -> None:
    manifests = plugin_manager.list_installed()
    if not manifests:
        print("No plugins installed.")
        return
    for m in manifests:
        name = m.get("name", "?")
        version = m.get("version", "?")
        desc = m.get("description", "")
        print(f"  {name} v{version} -- {desc}")


def cmd_update(name: str) -> None:
    try:
        manifest = asyncio.run(plugin_manager.update(name))
    except plugin_manager.PluginError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    print(f"Updated '{name}' to v{manifest.get('version', '?')}")
    print("Restart Light CC to apply changes.")


def cmd_uninstall(name: str) -> None:
    try:
        asyncio.run(plugin_manager.uninstall(name))
    except plugin_manager.PluginError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    print(f"Uninstalled '{name}'. Restart Light CC to complete removal.")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "install":
        if len(sys.argv) < 3:
            print("Usage: plugin_cli.py install <url-or-path>")
            sys.exit(1)
        cmd_install(sys.argv[2])
    elif cmd == "list":
        cmd_list()
    elif cmd == "update":
        if len(sys.argv) < 3:
            print("Usage: plugin_cli.py update <name>")
            sys.exit(1)
        cmd_update(sys.argv[2])
    elif cmd in ("uninstall", "remove"):
        if len(sys.argv) < 3:
            print("Usage: plugin_cli.py uninstall <name>")
            sys.exit(1)
        cmd_uninstall(sys.argv[2])
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
