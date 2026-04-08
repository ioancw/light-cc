#!/usr/bin/env python3
"""Plugin management CLI for Light CC.

Usage:
    python scripts/plugin_cli.py install <url-or-path>
    python scripts/plugin_cli.py list
    python scripts/plugin_cli.py update <name>
    python scripts/plugin_cli.py uninstall <name>
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Default plugins directory
PLUGINS_DIR = Path(__file__).resolve().parent.parent / "plugins"


def _validate_manifest(manifest_path: Path) -> dict | None:
    """Parse and validate a plugin manifest. Returns manifest dict or None."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ERROR: Failed to parse {manifest_path}: {e}")
        return None

    name = manifest.get("name")
    if not name:
        print("  ERROR: Manifest missing 'name' field")
        return None

    if not re.match(r"^[a-z0-9][a-z0-9-]*$", name):
        print(f"  ERROR: Invalid plugin name '{name}'. Must match ^[a-z0-9][a-z0-9-]*$")
        return None

    version = manifest.get("version")
    if not version:
        print("  ERROR: Manifest missing 'version' field")
        return None

    return manifest


def _install_dependencies(manifest: dict, plugin_dir: Path) -> None:
    """Install Python and NPM dependencies declared in the manifest."""
    deps = manifest.get("dependencies", {})

    python_deps = deps.get("python", [])
    if python_deps:
        print(f"  Installing Python dependencies: {python_deps}")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", *python_deps],
            check=True,
            capture_output=True,
        )

    npm_deps = deps.get("npm", [])
    if npm_deps:
        print(f"  Installing NPM dependencies: {npm_deps}")
        subprocess.run(
            ["npm", "install", *npm_deps],
            cwd=str(plugin_dir),
            check=True,
            capture_output=True,
        )


def cmd_install(source: str) -> None:
    """Install a plugin from a git URL or local path."""
    source_path = Path(source)

    if source_path.exists() and source_path.is_dir():
        # Local directory install
        manifest_path = source_path / ".claude-plugin" / "plugin.json"
        if not manifest_path.exists():
            print(f"ERROR: No plugin manifest found at {manifest_path}")
            sys.exit(1)

        manifest = _validate_manifest(manifest_path)
        if not manifest:
            sys.exit(1)

        name = manifest["name"]
        dest = PLUGINS_DIR / name

        if dest.exists():
            print(f"ERROR: Plugin '{name}' already installed at {dest}")
            print("  Use 'update' to update it, or 'uninstall' first.")
            sys.exit(1)

        PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_path, dest)
        print(f"Installed '{name}' v{manifest.get('version', '?')} from local path")

    elif source.startswith("http") or source.endswith(".git"):
        # Git clone
        PLUGINS_DIR.mkdir(parents=True, exist_ok=True)

        # Clone to a temp name first, then rename based on manifest
        tmp_dir = PLUGINS_DIR / "_installing"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)

        print(f"Cloning {source}...")
        result = subprocess.run(
            ["git", "clone", "--depth", "1", source, str(tmp_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"ERROR: git clone failed:\n{result.stderr}")
            sys.exit(1)

        manifest_path = tmp_dir / ".claude-plugin" / "plugin.json"
        if not manifest_path.exists():
            shutil.rmtree(tmp_dir)
            print("ERROR: Cloned repo has no .claude-plugin/plugin.json")
            sys.exit(1)

        manifest = _validate_manifest(manifest_path)
        if not manifest:
            shutil.rmtree(tmp_dir)
            sys.exit(1)

        name = manifest["name"]
        dest = PLUGINS_DIR / name

        if dest.exists():
            shutil.rmtree(tmp_dir)
            print(f"ERROR: Plugin '{name}' already installed. Use 'update' or 'uninstall' first.")
            sys.exit(1)

        tmp_dir.rename(dest)
        print(f"Installed '{name}' v{manifest.get('version', '?')} from git")

    else:
        print(f"ERROR: '{source}' is not a valid path or git URL")
        sys.exit(1)

    # Install dependencies
    _install_dependencies(manifest, dest)
    print("Done. Restart Light CC to activate the plugin.")


def cmd_list() -> None:
    """List all installed plugins."""
    if not PLUGINS_DIR.exists():
        print("No plugins directory found.")
        return

    found = False
    for entry in sorted(PLUGINS_DIR.iterdir()):
        manifest_path = entry / ".claude-plugin" / "plugin.json"
        if not manifest_path.exists():
            continue

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            name = manifest.get("name", entry.name)
            version = manifest.get("version", "?")
            desc = manifest.get("description", "")
            print(f"  {name} v{version} -- {desc}")
            found = True
        except Exception:
            print(f"  {entry.name} (invalid manifest)")
            found = True

    if not found:
        print("No plugins installed.")


def cmd_update(name: str) -> None:
    """Update a plugin by pulling the latest from its git origin."""
    plugin_dir = PLUGINS_DIR / name
    if not plugin_dir.exists():
        print(f"ERROR: Plugin '{name}' not found in {PLUGINS_DIR}")
        sys.exit(1)

    git_dir = plugin_dir / ".git"
    if not git_dir.exists():
        print(f"ERROR: Plugin '{name}' was not installed from git. Reinstall to update.")
        sys.exit(1)

    print(f"Updating '{name}'...")
    result = subprocess.run(
        ["git", "pull", "--ff-only"],
        cwd=str(plugin_dir),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: git pull failed:\n{result.stderr}")
        sys.exit(1)

    # Re-validate manifest
    manifest_path = plugin_dir / ".claude-plugin" / "plugin.json"
    manifest = _validate_manifest(manifest_path)
    if manifest:
        _install_dependencies(manifest, plugin_dir)
        print(f"Updated '{name}' to v{manifest.get('version', '?')}")
    else:
        print("WARNING: Plugin updated but manifest is invalid")

    print("Restart Light CC to apply changes.")


def cmd_uninstall(name: str) -> None:
    """Remove an installed plugin."""
    plugin_dir = PLUGINS_DIR / name
    if not plugin_dir.exists():
        print(f"ERROR: Plugin '{name}' not found in {PLUGINS_DIR}")
        sys.exit(1)

    shutil.rmtree(plugin_dir)
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
