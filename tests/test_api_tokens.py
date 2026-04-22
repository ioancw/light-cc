"""Tests for API tokens (core/api_tokens.py + /api/tokens routes + extended auth)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from core.api_tokens import (
    TOKEN_PREFIX,
    _hash_token,
    create_api_token,
    list_api_tokens,
    revoke_api_token,
    verify_api_token,
)
from core.auth import create_access_token
from core.db_models import ApiToken
from routes.api_tokens import router as api_tokens_router
from routes.auth import get_current_user


# ── CRUD helpers ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def tokens_db(test_db: AsyncSession, test_user):
    @asynccontextmanager
    async def _get_test_db():
        yield test_db

    with patch("core.api_tokens.get_db", side_effect=_get_test_db):
        yield test_db, test_user


class TestCreateApiToken:
    @pytest.mark.asyncio
    async def test_create_returns_plaintext_and_row(self, tokens_db):
        _, user = tokens_db
        row, plaintext = await create_api_token(user.id, "ci-bot")

        assert plaintext.startswith(TOKEN_PREFIX)
        assert len(plaintext) > len(TOKEN_PREFIX) + 20
        assert row.name == "ci-bot"
        assert row.user_id == user.id
        assert row.prefix == plaintext[:12]
        assert row.token_hash == _hash_token(plaintext)
        assert row.revoked_at is None

    @pytest.mark.asyncio
    async def test_create_rejects_empty_name(self, tokens_db):
        _, user = tokens_db
        with pytest.raises(ValueError):
            await create_api_token(user.id, "")
        with pytest.raises(ValueError):
            await create_api_token(user.id, "   ")

    @pytest.mark.asyncio
    async def test_create_strips_name(self, tokens_db):
        _, user = tokens_db
        row, _ = await create_api_token(user.id, "  spaced  ")
        assert row.name == "spaced"

    @pytest.mark.asyncio
    async def test_each_token_is_unique(self, tokens_db):
        _, user = tokens_db
        _, t1 = await create_api_token(user.id, "a")
        _, t2 = await create_api_token(user.id, "b")
        assert t1 != t2


class TestListApiTokens:
    @pytest.mark.asyncio
    async def test_list_empty(self, tokens_db):
        _, user = tokens_db
        assert await list_api_tokens(user.id) == []

    @pytest.mark.asyncio
    async def test_list_returns_user_tokens_only(self, tokens_db):
        db, user = tokens_db
        await create_api_token(user.id, "mine-1")
        await create_api_token(user.id, "mine-2")

        from core.auth import hash_password
        from core.db_models import User
        other = User(email="o@x.com", password_hash=hash_password("x"), display_name="O")
        db.add(other)
        await db.commit()
        await db.refresh(other)
        await create_api_token(other.id, "theirs")

        rows = await list_api_tokens(user.id)
        assert {r.name for r in rows} == {"mine-1", "mine-2"}


class TestRevokeApiToken:
    @pytest.mark.asyncio
    async def test_revoke_marks_revoked_at(self, tokens_db):
        _, user = tokens_db
        row, _ = await create_api_token(user.id, "to-revoke")
        assert await revoke_api_token(user.id, row.id) is True

        rows = await list_api_tokens(user.id)
        assert rows[0].revoked_at is not None

    @pytest.mark.asyncio
    async def test_revoke_idempotent(self, tokens_db):
        _, user = tokens_db
        row, _ = await create_api_token(user.id, "x")
        assert await revoke_api_token(user.id, row.id) is True
        assert await revoke_api_token(user.id, row.id) is False

    @pytest.mark.asyncio
    async def test_revoke_wrong_user(self, tokens_db):
        db, user = tokens_db
        row, _ = await create_api_token(user.id, "x")

        from core.auth import hash_password
        from core.db_models import User
        intruder = User(email="i@x.com", password_hash=hash_password("x"), display_name="I")
        db.add(intruder)
        await db.commit()
        await db.refresh(intruder)

        assert await revoke_api_token(intruder.id, row.id) is False

    @pytest.mark.asyncio
    async def test_revoke_unknown_id(self, tokens_db):
        _, user = tokens_db
        assert await revoke_api_token(user.id, "nonexistent") is False


class TestVerifyApiToken:
    @pytest.mark.asyncio
    async def test_verify_valid_returns_user(self, tokens_db):
        _, user = tokens_db
        _, plaintext = await create_api_token(user.id, "valid")

        resolved = await verify_api_token(plaintext)
        assert resolved is not None
        assert resolved.id == user.id

    @pytest.mark.asyncio
    async def test_verify_updates_last_used_at(self, tokens_db):
        _, user = tokens_db
        row, plaintext = await create_api_token(user.id, "used")
        assert row.last_used_at is None

        await verify_api_token(plaintext)

        rows = await list_api_tokens(user.id)
        assert rows[0].last_used_at is not None

    @pytest.mark.asyncio
    async def test_verify_revoked_returns_none(self, tokens_db):
        _, user = tokens_db
        row, plaintext = await create_api_token(user.id, "r")
        await revoke_api_token(user.id, row.id)

        assert await verify_api_token(plaintext) is None

    @pytest.mark.asyncio
    async def test_verify_expired_returns_none(self, tokens_db):
        _, user = tokens_db
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        await create_api_token(user.id, "exp", expires_at=past)

        # Find the plaintext via a fresh create (we need the plaintext)
        _, plaintext = await create_api_token(user.id, "also-exp", expires_at=past)
        assert await verify_api_token(plaintext) is None

    @pytest.mark.asyncio
    async def test_verify_unknown_returns_none(self, tokens_db):
        assert await verify_api_token("lcc_nonsense") is None

    @pytest.mark.asyncio
    async def test_verify_no_prefix_returns_none(self, tokens_db):
        assert await verify_api_token("not-a-token") is None
        assert await verify_api_token("") is None


# ── HTTP routes ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def tokens_api_client(test_db: AsyncSession, test_user):
    @asynccontextmanager
    async def _get_test_db():
        yield test_db

    app = FastAPI()
    app.include_router(api_tokens_router)
    app.dependency_overrides[get_current_user] = lambda: test_user

    with patch("core.api_tokens.get_db", side_effect=_get_test_db):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, test_db, test_user


class TestTokenRoutes:
    @pytest.mark.asyncio
    async def test_create_returns_plaintext_once(self, tokens_api_client):
        client, _, _ = tokens_api_client
        resp = await client.post("/api/tokens", json={"name": "ci"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["token"].startswith("lcc_")
        assert body["name"] == "ci"
        assert body["revoked_at"] is None

        # List endpoint never returns plaintext
        resp = await client.get("/api/tokens")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert "token" not in rows[0]
        assert rows[0]["prefix"].startswith("lcc_")

    @pytest.mark.asyncio
    async def test_create_rejects_empty_name(self, tokens_api_client):
        client, _, _ = tokens_api_client
        resp = await client.post("/api/tokens", json={"name": ""})
        assert resp.status_code == 422  # pydantic min_length

    @pytest.mark.asyncio
    async def test_revoke_success(self, tokens_api_client):
        client, _, _ = tokens_api_client
        created = (await client.post("/api/tokens", json={"name": "r"})).json()

        resp = await client.delete(f"/api/tokens/{created['id']}")
        assert resp.status_code == 204

        # Listed row now has revoked_at set
        rows = (await client.get("/api/tokens")).json()
        assert rows[0]["revoked_at"] is not None

    @pytest.mark.asyncio
    async def test_revoke_unknown_404(self, tokens_api_client):
        client, _, _ = tokens_api_client
        resp = await client.delete("/api/tokens/nope")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_revoke_twice_404(self, tokens_api_client):
        client, _, _ = tokens_api_client
        created = (await client.post("/api/tokens", json={"name": "r"})).json()
        await client.delete(f"/api/tokens/{created['id']}")
        resp = await client.delete(f"/api/tokens/{created['id']}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_create_rate_limited_after_burst(self, tokens_api_client):
        """5 creations per hour cap — the 6th returns 429."""
        from core.rate_limit import reset_limits
        client, _, user = tokens_api_client
        reset_limits(user.id)
        try:
            for i in range(5):
                resp = await client.post("/api/tokens", json={"name": f"tok-{i}"})
                assert resp.status_code == 201

            resp = await client.post("/api/tokens", json={"name": "over"})
            assert resp.status_code == 429
            assert "token creation" in resp.json()["detail"].lower()
        finally:
            reset_limits(user.id)


# ── get_current_user dual-path ──────────────────────────────────────────


@pytest_asyncio.fixture
async def auth_test_app(test_db: AsyncSession, test_user):
    """An app with a protected echo endpoint using the real get_current_user."""
    from fastapi import Depends
    from core.db_models import User as UserModel

    @asynccontextmanager
    async def _get_test_db():
        yield test_db

    app = FastAPI()

    @app.get("/whoami")
    async def whoami(user: UserModel = Depends(get_current_user)):
        return {"id": user.id, "email": user.email}

    with patch("core.api_tokens.get_db", side_effect=_get_test_db), \
         patch("core.database.get_db", side_effect=_get_test_db), \
         patch("routes.auth.get_db", side_effect=_get_test_db):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, test_db, test_user


class TestGetCurrentUserDualPath:
    @pytest.mark.asyncio
    async def test_jwt_still_works(self, auth_test_app, mock_redis):
        client, _, user = auth_test_app
        jwt = create_access_token(user.id, user.email)
        resp = await client.get("/whoami", headers={"Authorization": f"Bearer {jwt}"})
        assert resp.status_code == 200
        assert resp.json()["id"] == user.id

    @pytest.mark.asyncio
    async def test_api_token_works(self, auth_test_app):
        client, _, user = auth_test_app
        _, plaintext = await create_api_token(user.id, "auth-test")

        resp = await client.get("/whoami", headers={"Authorization": f"Bearer {plaintext}"})
        assert resp.status_code == 200
        assert resp.json()["id"] == user.id

    @pytest.mark.asyncio
    async def test_revoked_api_token_rejected(self, auth_test_app):
        client, _, user = auth_test_app
        row, plaintext = await create_api_token(user.id, "revoke-me")
        await revoke_api_token(user.id, row.id)

        resp = await client.get("/whoami", headers={"Authorization": f"Bearer {plaintext}"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_unknown_api_token_rejected(self, auth_test_app):
        client, _, _ = auth_test_app
        resp = await client.get("/whoami", headers={"Authorization": "Bearer lcc_unknown"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_non_prefix_non_jwt_rejected(self, auth_test_app):
        client, _, _ = auth_test_app
        resp = await client.get("/whoami", headers={"Authorization": "Bearer garbage"})
        assert resp.status_code == 401
