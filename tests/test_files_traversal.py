"""Hardening tests for /api/files — traversal, upload size/ext, signed downloads.

Covers S4 of the security plan. Uses httpx + ASGI transport to hit the FastAPI
router directly with an in-memory DB and a temp workspace.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from core.db_models import User
from core.sandbox import UserWorkspace
from routes.auth import get_current_user
from routes.files import router as files_router


@pytest_asyncio.fixture
async def file_api(tmp_path: Path, test_user: User):
    """FastAPI client with /api/files mounted against a tmp workspace."""
    ws_root = tmp_path / "users" / test_user.id / "workspace"
    outputs = tmp_path / "users" / test_user.id / "outputs"
    uploads = tmp_path / "users" / test_user.id / "uploads"
    memory = tmp_path / "users" / test_user.id / "memory"
    for d in (ws_root, outputs, uploads, memory):
        d.mkdir(parents=True, exist_ok=True)

    (ws_root / "hello.txt").write_text("hello world")

    # A sibling directory that shares the workspace's name prefix — the old
    # `startswith` check would pass this traversal.
    sibling = tmp_path / "users" / test_user.id / "workspace_evil"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("SECRET")

    def _fake_workspace(user_id: str) -> UserWorkspace:
        ws = UserWorkspace.__new__(UserWorkspace)
        ws.user_id = user_id
        ws.root = ws_root
        ws.outputs = outputs
        ws.uploads = uploads
        ws.memory = memory
        return ws

    app = FastAPI()
    app.include_router(files_router)
    app.dependency_overrides[get_current_user] = lambda: test_user

    with patch("routes.files.get_workspace", side_effect=_fake_workspace):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, ws_root


class TestTraversal:
    @pytest.mark.asyncio
    async def test_sibling_prefix_rejected(self, file_api):
        """`../workspace_evil/secret.txt` must not resolve inside workspace."""
        client, _ = file_api
        resp = await client.get("/api/files/read", params={"path": "../workspace_evil/secret.txt"})
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_absolute_path_rejected(self, file_api):
        client, _ = file_api
        resp = await client.get("/api/files/read", params={"path": "/etc/passwd"})
        # Either 403 (outside) or 404 (doesn't exist after coerce) — never leak contents.
        assert resp.status_code in (403, 404)

    @pytest.mark.asyncio
    async def test_null_byte_rejected(self, file_api):
        client, _ = file_api
        resp = await client.get("/api/files/read", params={"path": "hello.txt\x00.jpg"})
        assert resp.status_code == 400


class TestUpload:
    @pytest.mark.asyncio
    async def test_blocked_extension_rejected(self, file_api):
        client, _ = file_api
        files = {"file": ("evil.html", b"<script>alert(1)</script>", "text/html")}
        resp = await client.post("/api/files/upload", files=files)
        assert resp.status_code == 400
        assert ".html" in resp.text.lower() or "not allowed" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_size_cap_enforced(self, file_api):
        from core.config import settings
        client, ws_root = file_api
        oversize = settings.files.max_upload_mb * 1024 * 1024 + 1024
        payload = b"a" * oversize
        files = {"file": ("big.bin", payload, "application/octet-stream")}
        resp = await client.post("/api/files/upload", files=files)
        assert resp.status_code == 413
        # Partial file must not linger.
        assert not (ws_root / "big.bin").exists()

    @pytest.mark.asyncio
    async def test_under_cap_succeeds(self, file_api):
        client, ws_root = file_api
        files = {"file": ("notes.txt", b"hi", "text/plain")}
        resp = await client.post("/api/files/upload", files=files)
        assert resp.status_code == 200
        assert (ws_root / "notes.txt").read_bytes() == b"hi"


class TestDownload:
    @pytest.mark.asyncio
    async def test_access_jwt_in_query_rejected(self, file_api):
        """The old `?token=<access JWT>` download flow must be gone."""
        client, _ = file_api
        resp = await client.get(
            "/api/files/download",
            params={"path": "hello.txt", "token": "irrelevant"},
        )
        # `sig` is now required; the old `token` param is not.
        assert resp.status_code in (401, 422)

    @pytest.mark.asyncio
    async def test_download_url_roundtrip(self, file_api):
        client, _ = file_api
        resp = await client.post("/api/files/download-url", json={"path": "hello.txt"})
        assert resp.status_code == 200
        url = resp.json()["url"]
        assert url.startswith("/api/files/download?sig=")

        resp2 = await client.get(url)
        assert resp2.status_code == 200
        assert resp2.content == b"hello world"
        assert resp2.headers.get("x-content-type-options") == "nosniff"
        assert "attachment" in resp2.headers.get("content-disposition", "").lower()

    @pytest.mark.asyncio
    async def test_download_url_for_missing_file_404s(self, file_api):
        client, _ = file_api
        resp = await client.post("/api/files/download-url", json={"path": "nope.txt"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_download_url_rejects_traversal(self, file_api):
        client, _ = file_api
        resp = await client.post(
            "/api/files/download-url",
            json={"path": "../workspace_evil/secret.txt"},
        )
        assert resp.status_code == 403
