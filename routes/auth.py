"""Auth HTTP endpoints: register, login, refresh, me."""

from __future__ import annotations

import time
from collections import defaultdict

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

router = APIRouter(prefix="/api/auth", tags=["auth"])
_bearer = HTTPBearer()


# ── IP-based rate limiting for auth endpoints ────────────────────────

_AUTH_MAX_ATTEMPTS = 10  # per window
_AUTH_WINDOW_SECONDS = 300  # 5 minutes
_auth_attempts: dict[str, list[float]] = defaultdict(list)


def _check_auth_rate_limit(request: Request) -> None:
    """Raise 429 if the IP has exceeded auth attempt limits."""
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    cutoff = now - _AUTH_WINDOW_SECONDS

    # Prune old entries
    attempts = _auth_attempts[ip]
    _auth_attempts[ip] = [t for t in attempts if t > cutoff]

    if len(_auth_attempts[ip]) >= _AUTH_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")

    _auth_attempts[ip].append(now)


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
    payload = decode_token(creds.credentials)
    if payload is None or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if await is_token_revoked(creds.credentials):
        raise HTTPException(status_code=401, detail="Token has been revoked")
    db = await get_db()
    try:
        user = await get_user_by_id(db, payload["sub"])
    finally:
        await db.close()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/register", response_model=TokenResponse, dependencies=[Depends(_check_auth_rate_limit)])
async def register(req: RegisterRequest):
    if not settings.auth.registration_enabled:
        raise HTTPException(status_code=403, detail="Registration is disabled")

    db = await get_db()
    try:
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
    finally:
        await db.close()

    # Seed YAML-defined agents for the new user, so they see the shipped
    # example agents in AgentPanel on first login. Best-effort.
    try:
        from pathlib import Path
        from core.agent_loader import sync_agents_to_db

        _project_root = Path(__file__).resolve().parent.parent
        for agents_dir in settings.paths.agents_dirs:
            resolved = Path(agents_dir).expanduser()
            if not resolved.is_absolute():
                resolved = _project_root / resolved
            if resolved.exists():
                await sync_agents_to_db(resolved, user.id)
    except Exception:
        pass

    return TokenResponse(
        access_token=create_access_token(user.id, user.email),
        refresh_token=create_refresh_token(user.id),
        user=UserResponse(id=user.id, email=user.email, display_name=user.display_name, is_admin=user.is_admin),
    )


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(_check_auth_rate_limit)])
async def login(req: LoginRequest):
    db = await get_db()
    try:
        user = await authenticate_user(db, req.email, req.password)
    finally:
        await db.close()

    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    return TokenResponse(
        access_token=create_access_token(user.id, user.email),
        refresh_token=create_refresh_token(user.id),
        user=UserResponse(id=user.id, email=user.email, display_name=user.display_name, is_admin=user.is_admin),
    )


@router.post("/refresh", response_model=TokenResponse, dependencies=[Depends(_check_auth_rate_limit)])
async def refresh(req: RefreshRequest):
    payload = decode_token(req.refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    if await is_token_revoked(req.refresh_token):
        raise HTTPException(status_code=401, detail="Refresh token has been revoked")

    db = await get_db()
    try:
        user = await get_user_by_id(db, payload["sub"])
    finally:
        await db.close()

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
