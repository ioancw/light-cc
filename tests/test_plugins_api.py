"""Tests for the /api/plugins REST endpoints (routes/plugins.py)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from core.db_models import User
from core.plugin_loader import PluginLoader
from routes.auth import get_current_user
from routes.admin import require_admin
from routes.plugins import router as plugins_router


@pytest_asyncio.fixture
async def admin_user(test_db: AsyncSession) -> User:
    user = User(
        email="admin@example.com",
        password_hash="x",
        display_name="Admin",
        is_admin=True,
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)
    return user


@pytest_asyncio.fixture
async def regular_user(test_db: AsyncSession) -> User:
    user = User(
        email="reg@example.com",
        password_hash="x",
        display_name="Regular",
        is_admin=False,
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)
    return user


def _write_plugin(plugins_dir: Path, name: str, version: str = "1.0.0") -> Path:
    plugin = plugins_dir / name
    plugin.mkdir(parents=True, exist_ok=True)
    manifest_dir = plugin / ".claude-plugin"
    manifest_dir.mkdir()
    (manifest_dir / "plugin.json").write_text(json.dumps({
        "name": name,
        "version": version,
        "description": f"Test plugin {name}",
        "author": "tester",
    }))
    return plugin


@pytest_asyncio.fixture
async def api_client(tmp_path: Path, admin_user, regular_user, test_db):
    """API client bound to a tmp plugins dir and an isolated PluginLoader."""

    @asynccontextmanager
    async def _get_test_db():
        yield test_db

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()

    loader = PluginLoader()

    app = FastAPI()
    app.include_router(plugins_router)

    # Default to admin; tests can override per-call.
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[require_admin] = lambda: admin_user

    with patch("core.plugin_manager._resolve_plugins_dir", return_value=plugins_dir), \
         patch("core.plugin_loader._loader", loader), \
         patch("core.database.get_db", side_effect=_get_test_db):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, plugins_dir, loader, app, admin_user, regular_user


class TestList:
    @pytest.mark.asyncio
    async def test_list_empty(self, api_client):
        client, *_ = api_client
        resp = await client.get("/api/plugins")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_list_returns_installed(self, api_client):
        client, plugins_dir, *_ = api_client
        _write_plugin(plugins_dir, "p-one")
        _write_plugin(plugins_dir, "p-two", version="2.0.0")

        resp = await client.get("/api/plugins")
        assert resp.status_code == 200
        names = sorted(p["name"] for p in resp.json())
        assert names == ["p-one", "p-two"]

    @pytest.mark.asyncio
    async def test_list_marks_loaded_state(self, api_client):
        client, plugins_dir, loader, *_ = api_client
        plugin_path = _write_plugin(plugins_dir, "loaded-one")
        await loader.load_plugin(plugin_path)
        _write_plugin(plugins_dir, "unloaded-one")

        resp = await client.get("/api/plugins")
        items = {p["name"]: p for p in resp.json()}
        assert items["loaded-one"]["loaded"] is True
        assert items["unloaded-one"]["loaded"] is False


class TestGet:
    @pytest.mark.asyncio
    async def test_get_found(self, api_client):
        client, plugins_dir, *_ = api_client
        _write_plugin(plugins_dir, "the-plugin", version="3.1.4")

        resp = await client.get("/api/plugins/the-plugin")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "the-plugin"
        assert body["version"] == "3.1.4"

    @pytest.mark.asyncio
    async def test_get_missing(self, api_client):
        client, *_ = api_client
        resp = await client.get("/api/plugins/missing")
        assert resp.status_code == 404


class TestInstall:
    @pytest.mark.asyncio
    async def test_install_local_path(self, api_client, tmp_path):
        client, plugins_dir, loader, *_ = api_client
        # Create a source plugin OUTSIDE the plugins dir
        src = tmp_path / "src" / "fresh-plugin"
        _write_plugin(src.parent, "fresh-plugin")

        resp = await client.post("/api/plugins/install", json={"source": str(src)})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "fresh-plugin"
        assert body["loaded"] is True
        # Files copied
        assert (plugins_dir / "fresh-plugin" / ".claude-plugin" / "plugin.json").exists()

    @pytest.mark.asyncio
    async def test_install_invalid_source(self, api_client):
        client, *_ = api_client
        resp = await client.post(
            "/api/plugins/install", json={"source": "/no/such/dir"}
        )
        assert resp.status_code == 400


class TestUninstall:
    @pytest.mark.asyncio
    async def test_uninstall_removes_files_and_unloads(self, api_client):
        client, plugins_dir, loader, *_ = api_client
        plugin_path = _write_plugin(plugins_dir, "to-remove")
        await loader.load_plugin(plugin_path)
        assert loader.get_plugin("to-remove") is not None

        resp = await client.delete("/api/plugins/to-remove")
        assert resp.status_code == 204
        assert not plugin_path.exists()
        assert loader.get_plugin("to-remove") is None

    @pytest.mark.asyncio
    async def test_uninstall_missing(self, api_client):
        client, *_ = api_client
        resp = await client.delete("/api/plugins/never-installed")
        assert resp.status_code == 404


class TestReload:
    @pytest.mark.asyncio
    async def test_reload_picks_up_changes(self, api_client):
        client, plugins_dir, loader, *_ = api_client
        plugin_path = _write_plugin(plugins_dir, "reloadable", version="1.0.0")
        await loader.load_plugin(plugin_path)
        assert loader.get_plugin("reloadable").version == "1.0.0"

        # Mutate the manifest on disk
        (plugin_path / ".claude-plugin" / "plugin.json").write_text(json.dumps({
            "name": "reloadable",
            "version": "2.0.0",
            "description": "updated",
        }))

        resp = await client.post("/api/plugins/reloadable/reload")
        assert resp.status_code == 200
        assert resp.json()["version"] == "2.0.0"
        assert loader.get_plugin("reloadable").version == "2.0.0"

    @pytest.mark.asyncio
    async def test_reload_missing(self, api_client):
        client, *_ = api_client
        resp = await client.post("/api/plugins/missing/reload")
        assert resp.status_code == 404


class TestAdminGate:
    @pytest.mark.asyncio
    async def test_non_admin_blocked_from_install(self, api_client):
        client, plugins_dir, loader, app, admin_user, regular_user = api_client
        # Swap the admin override to enforce the real check against a regular user
        from fastapi import HTTPException

        def _deny():
            raise HTTPException(status_code=403, detail="Admin access required")

        app.dependency_overrides[require_admin] = _deny

        resp = await client.post(
            "/api/plugins/install", json={"source": "/anything"}
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_non_admin_can_list(self, api_client):
        client, plugins_dir, *_ = api_client
        _write_plugin(plugins_dir, "visible")
        resp = await client.get("/api/plugins")
        assert resp.status_code == 200
        assert any(p["name"] == "visible" for p in resp.json())
