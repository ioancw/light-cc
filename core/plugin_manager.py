"""Reusable async helpers for plugin install / update / uninstall.

This module is the canonical source of plugin lifecycle logic. Both the CLI
(scripts/plugin_cli.py) and the REST API (routes/plugins.py) call into here.

All functions raise PluginError on failure with a human-readable message.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

from core.config import settings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class PluginError(Exception):
    """Raised by plugin manager operations. Message is safe to surface to users."""


def _resolve_plugins_dir() -> Path:
    """Return the configured plugins directory, resolved to an absolute path."""
    raw = settings.paths.plugins_dirs[0] if settings.paths.plugins_dirs else "plugins"
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p


def _validate_manifest(manifest_path: Path) -> dict:
    """Parse + validate a plugin manifest. Raises PluginError on any problem."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise PluginError(f"Failed to parse {manifest_path}: {e}")

    name = manifest.get("name")
    if not name:
        raise PluginError("Manifest missing 'name' field")
    if not _NAME_RE.match(name):
        raise PluginError(
            f"Invalid plugin name '{name}'. Must match {_NAME_RE.pattern}"
        )
    if not manifest.get("version"):
        raise PluginError("Manifest missing 'version' field")

    return manifest


async def _install_dependencies(manifest: dict, plugin_dir: Path) -> None:
    """Install Python and NPM dependencies declared in the manifest."""
    deps = manifest.get("dependencies", {})
    python_deps = deps.get("python", [])
    npm_deps = deps.get("npm", [])

    if python_deps:
        logger.info(f"Installing Python deps: {python_deps}")
        await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "pip", "install", *python_deps],
            check=True,
            capture_output=True,
        )

    if npm_deps:
        logger.info(f"Installing NPM deps: {npm_deps}")
        await asyncio.to_thread(
            subprocess.run,
            ["npm", "install", *npm_deps],
            cwd=str(plugin_dir),
            check=True,
            capture_output=True,
        )


async def install_from_path(source: Path) -> dict:
    """Install a plugin from a local directory. Returns the manifest."""
    if not source.exists() or not source.is_dir():
        raise PluginError(f"'{source}' is not an existing directory")

    manifest_path = source / ".claude-plugin" / "plugin.json"
    if not manifest_path.exists():
        raise PluginError(f"No plugin manifest at {manifest_path}")

    manifest = _validate_manifest(manifest_path)
    name = manifest["name"]
    plugins_dir = _resolve_plugins_dir()
    dest = plugins_dir / name

    if dest.exists():
        raise PluginError(
            f"Plugin '{name}' already installed. Use update or uninstall first."
        )

    plugins_dir.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.copytree, source, dest)
    await _install_dependencies(manifest, dest)
    return manifest


async def install_from_git(url: str) -> dict:
    """Install a plugin by cloning a git URL. Returns the manifest."""
    plugins_dir = _resolve_plugins_dir()
    plugins_dir.mkdir(parents=True, exist_ok=True)

    tmp_dir = plugins_dir / "_installing"
    if tmp_dir.exists():
        await asyncio.to_thread(shutil.rmtree, tmp_dir)

    logger.info(f"Cloning {url} -> {tmp_dir}")
    result = await asyncio.to_thread(
        subprocess.run,
        ["git", "clone", "--depth", "1", url, str(tmp_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        await asyncio.to_thread(shutil.rmtree, tmp_dir, True)
        raise PluginError(f"git clone failed: {result.stderr.strip()}")

    manifest_path = tmp_dir / ".claude-plugin" / "plugin.json"
    if not manifest_path.exists():
        await asyncio.to_thread(shutil.rmtree, tmp_dir, True)
        raise PluginError("Cloned repo has no .claude-plugin/plugin.json")

    try:
        manifest = _validate_manifest(manifest_path)
    except PluginError:
        await asyncio.to_thread(shutil.rmtree, tmp_dir, True)
        raise

    name = manifest["name"]
    dest = plugins_dir / name
    if dest.exists():
        await asyncio.to_thread(shutil.rmtree, tmp_dir, True)
        raise PluginError(
            f"Plugin '{name}' already installed. Use update or uninstall first."
        )

    await asyncio.to_thread(tmp_dir.rename, dest)
    await _install_dependencies(manifest, dest)
    return manifest


async def install(source: str) -> dict:
    """Install from a git URL or local path. Auto-detects which."""
    p = Path(source)
    if p.exists() and p.is_dir():
        return await install_from_path(p)
    if source.startswith(("http://", "https://", "git@")) or source.endswith(".git"):
        return await install_from_git(source)
    raise PluginError(f"'{source}' is not a valid path or git URL")


async def update(name: str) -> dict:
    """Update an installed plugin via git pull. Returns the (possibly new) manifest."""
    plugins_dir = _resolve_plugins_dir()
    plugin_dir = plugins_dir / name
    if not plugin_dir.exists():
        raise PluginError(f"Plugin '{name}' not found in {plugins_dir}")
    if not (plugin_dir / ".git").exists():
        raise PluginError(
            f"Plugin '{name}' was not installed from git. Reinstall to update."
        )

    result = await asyncio.to_thread(
        subprocess.run,
        ["git", "pull", "--ff-only"],
        cwd=str(plugin_dir),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PluginError(f"git pull failed: {result.stderr.strip()}")

    manifest = _validate_manifest(plugin_dir / ".claude-plugin" / "plugin.json")
    await _install_dependencies(manifest, plugin_dir)
    return manifest


async def uninstall(name: str) -> None:
    """Remove an installed plugin's files. Caller must unload from runtime separately."""
    plugins_dir = _resolve_plugins_dir()
    plugin_dir = plugins_dir / name
    if not plugin_dir.exists():
        raise PluginError(f"Plugin '{name}' not found in {plugins_dir}")
    await asyncio.to_thread(shutil.rmtree, plugin_dir)


def list_installed() -> list[dict]:
    """List manifests of all installed plugins (filesystem view, not runtime)."""
    plugins_dir = _resolve_plugins_dir()
    if not plugins_dir.exists():
        return []

    out = []
    for entry in sorted(plugins_dir.iterdir()):
        manifest_path = entry / ".claude-plugin" / "plugin.json"
        if not manifest_path.exists():
            continue
        try:
            out.append(json.loads(manifest_path.read_text(encoding="utf-8")))
        except Exception as e:
            logger.warning(f"Skipping invalid manifest at {manifest_path}: {e}")
    return out
