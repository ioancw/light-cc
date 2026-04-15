"""Plugin registry REST API.

Endpoints:
    GET    /api/plugins                — list installed plugins (any logged-in user)
    GET    /api/plugins/{name}         — single plugin details (any logged-in user)
    POST   /api/plugins/install        — install from URL or local path (admin)
    POST   /api/plugins/{name}/update  — git pull + reload (admin)
    POST   /api/plugins/{name}/reload  — unload + reload from disk (admin)
    DELETE /api/plugins/{name}         — uninstall (admin)
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core import plugin_manager
from core.plugin_loader import get_plugin_loader
from routes.admin import require_admin
from routes.auth import User, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


# ── Schemas ───────────────────────────────────────────────────────────


class InstallRequest(BaseModel):
    source: str  # git URL or absolute local path


class PluginResponse(BaseModel):
    name: str
    version: str
    description: str
    author: str
    path: str
    loaded: bool
    skills: list[str]
    commands: list[str]
    agents: list[str]
    mcp_servers: list[str]


def _to_response(manifest: dict) -> PluginResponse:
    """Merge filesystem manifest with runtime PluginInfo for one consolidated view."""
    name = manifest.get("name", "")
    info = get_plugin_loader().get_plugin(name)
    return PluginResponse(
        name=name,
        version=manifest.get("version", "0.0.0"),
        description=manifest.get("description", ""),
        author=manifest.get("author", ""),
        path=str(info.path) if info else "",
        loaded=info is not None,
        skills=info.skills if info else [],
        commands=info.commands if info else [],
        agents=info.agents if info else [],
        mcp_servers=info.mcp_servers if info else [],
    )


# ── Endpoints ─────────────────────────────────────────────────────────


@router.get("", response_model=list[PluginResponse])
async def api_list_plugins(user: User = Depends(get_current_user)):
    return [_to_response(m) for m in plugin_manager.list_installed()]


@router.get("/{name}", response_model=PluginResponse)
async def api_get_plugin(name: str, user: User = Depends(get_current_user)):
    for m in plugin_manager.list_installed():
        if m.get("name") == name:
            return _to_response(m)
    raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")


@router.post("/install", response_model=PluginResponse, status_code=201)
async def api_install_plugin(
    req: InstallRequest, admin: User = Depends(require_admin)
):
    try:
        manifest = await plugin_manager.install(req.source)
    except plugin_manager.PluginError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Hot-load the freshly installed plugin so it's usable without restart.
    plugin_dir = Path(plugin_manager._resolve_plugins_dir()) / manifest["name"]
    try:
        await get_plugin_loader().load_plugin(plugin_dir)
    except Exception as e:
        logger.warning(f"Plugin installed but hot-load failed: {e}")

    return _to_response(manifest)


@router.post("/{name}/update", response_model=PluginResponse)
async def api_update_plugin(name: str, admin: User = Depends(require_admin)):
    try:
        manifest = await plugin_manager.update(name)
    except plugin_manager.PluginError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Reload to pick up changes
    loader = get_plugin_loader()
    await loader.unload_plugin(name)
    plugin_dir = Path(plugin_manager._resolve_plugins_dir()) / name
    try:
        await loader.load_plugin(plugin_dir)
    except Exception as e:
        logger.warning(f"Plugin updated but reload failed: {e}")

    return _to_response(manifest)


@router.post("/{name}/reload", response_model=PluginResponse)
async def api_reload_plugin(name: str, admin: User = Depends(require_admin)):
    plugin_dir = Path(plugin_manager._resolve_plugins_dir()) / name
    if not plugin_dir.exists():
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")

    loader = get_plugin_loader()
    await loader.unload_plugin(name)
    try:
        info = await loader.load_plugin(plugin_dir)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reload failed: {e}")
    if info is None:
        raise HTTPException(status_code=400, detail="Plugin has no valid manifest")

    manifest_path = plugin_dir / ".claude-plugin" / "plugin.json"
    import json as _json
    manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
    return _to_response(manifest)


@router.delete("/{name}", status_code=204)
async def api_uninstall_plugin(name: str, admin: User = Depends(require_admin)):
    loader = get_plugin_loader()
    await loader.unload_plugin(name)
    try:
        await plugin_manager.uninstall(name)
    except plugin_manager.PluginError as e:
        raise HTTPException(status_code=404, detail=str(e))
