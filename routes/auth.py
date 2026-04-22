"""Auth HTTP endpoints: register, login, refresh, me."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from core.auth import (
    authenticate_user,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_user_by_email,
    get_user_by_id,
    hash_password,
    is_token_revoked,
    revoke_token,
)
from core.config import settings
from core.database import get_db
from core.db_models import User
from core.rate_limit import _client_ip, check_auth_rate_limit

router = APIRouter(prefix="/api/auth", tags=["auth"])
_bearer = HTTPBearer()


async def _enforce_auth_rate_limit(request: Request, email: str | None) -> None:
    """Raise 429 (with Retry-After) if this (email, IP) pair is over limit."""
    ip = _client_ip(request)
    allowed, retry = await check_auth_rate_limit(email or "", ip)
    if not allowed:
        retry_int = max(int(retry), 1)
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Try again later.",
            headers={"Retry-After": str(retry_int)},
        )


# ── Request / response schemas ───────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    user: UserResponse


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    is_admin: bool


class RefreshRequest(BaseModel):
    refresh_token: str


# ── Dependency ────────────────────────────────────────────────────────

async def get_current_user(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> User:
    raw = creds.credentials

    # Long-lived API token path (opaque, revocable, no refresh flow).
    if raw.startswith("lcc_"):
        from core.api_tokens import verify_api_token
        user = await verify_api_token(raw)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or revoked API token")
        return user

    # JWT access token path (interactive sessions).
    payload = decode_token(raw)
    if payload is None or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if await is_token_revoked(raw):
        raise HTTPException(status_code=401, detail="Token has been revoked")
    async with get_db() as db:
        user = await get_user_by_id(db, payload["sub"])
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, request: Request):
    await _enforce_auth_rate_limit(request, req.email)
    if not settings.auth.registration_enabled:
        raise HTTPException(status_code=403, detail="Registration is disabled")

    async with get_db() as db:
        existing = await get_user_by_email(db, req.email)
        if existing:
            raise HTTPException(status_code=409, detail="Email already registered")

        user = User(
            email=req.email,
            password_hash=hash_password(req.password),
            display_name=req.display_name,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    # Seed YAML-defined agents for the new user, so they see the shipped
    # example agents in AgentPanel on first login. Best-effort.
    try:
        from pathlib import Path
        from core.agent_loader import discover_agents, sync_agent_defs_to_db, sync_agents_to_db
        from core.plugin_loader import get_plugin_loader

        _project_root = Path(__file__).resolve().parent.parent
        for agents_dir in settings.paths.agents_dirs:
            resolved = Path(agents_dir).expanduser()
            if not resolved.is_absolute():
                resolved = _project_root / resolved
            if resolved.exists():
                await sync_agents_to_db(resolved, user.id)

        # Also seed plugin-owned agents for the new user.
        for plugin_info in get_plugin_loader().list_plugins():
            plugin_agents_dir = plugin_info.path / "agents"
            if not plugin_agents_dir.exists():
                continue
            defs = discover_agents(plugin_agents_dir)
            for d in defs:
                d.name = f"{plugin_info.name}:{d.name}"
            if defs:
                await sync_agent_defs_to_db(
                    defs, user.id, source_label=f"plugin:{plugin_info.name}"
                )
    except Exception:
        pass

    return TokenResponse(
        access_token=create_access_token(user.id, user.email),
        refresh_token=create_refresh_token(user.id),
        user=UserResponse(id=user.id, email=user.email, display_name=user.display_name, is_admin=user.is_admin),
    )


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, request: Request):
    await _enforce_auth_rate_limit(request, req.email)
    async with get_db() as db:
        user = await authenticate_user(db, req.email, req.password)

    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    return TokenResponse(
        access_token=create_access_token(user.id, user.email),
        refresh_token=create_refresh_token(user.id),
        user=UserResponse(id=user.id, email=user.email, display_name=user.display_name, is_admin=user.is_admin),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(req: RefreshRequest, request: Request):
    # Refresh has no email in the request body; rate limit by IP only
    # (passing empty email just means the hash component is constant,
    # which is fine — the IP component still disambiguates actors).
    await _enforce_auth_rate_limit(request, email=None)
    payload = decode_token(req.refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    if await is_token_revoked(req.refresh_token):
        raise HTTPException(status_code=401, detail="Refresh token has been revoked")

    async with get_db() as db:
        user = await get_user_by_id(db, payload["sub"])

    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    # Revoke the old refresh token (token rotation)
    await revoke_token(req.refresh_token)

    return TokenResponse(
        access_token=create_access_token(user.id, user.email),
        refresh_token=create_refresh_token(user.id),
        user=UserResponse(id=user.id, email=user.email, display_name=user.display_name, is_admin=user.is_admin),
    )


@router.post("/logout")
async def logout(creds: HTTPAuthorizationCredentials = Depends(_bearer), body: RefreshRequest | None = None):
    """Revoke the current access token and optionally the refresh token."""
    token = creds.credentials
    payload = decode_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    revoked_access = await revoke_token(token)
    revoked_refresh = False
    if body and body.refresh_token:
        revoked_refresh = await revoke_token(body.refresh_token)
    return {"status": "logged_out", "revoked_access": revoked_access, "revoked_refresh": revoked_refresh}


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return UserResponse(id=user.id, email=user.email, display_name=user.display_name, is_admin=user.is_admin)
