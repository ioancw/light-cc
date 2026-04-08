"""JWT authentication and password hashing for Light CC."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db_models import User


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: str, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.auth.jwt_expiry_hours)
    payload = {
        "sub": user_id,
        "email": email,
        "exp": expire,
        "type": "access",
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.auth.jwt_refresh_expiry_days)
    payload = {
        "sub": user_id,
        "exp": expire,
        "type": "refresh",
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


_REVOKED_TOKENS_KEY = "lcc:revoked_tokens"


async def revoke_token(token: str) -> bool:
    """Revoke a JWT by adding its jti to the Redis revocation set.

    Returns True if revoked successfully, False if Redis unavailable or token invalid.
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return False

    jti = payload.get("jti")
    if not jti:
        return False

    # TTL = time remaining until token expires
    exp = payload.get("exp")
    if exp:
        remaining = int(exp - datetime.now(timezone.utc).timestamp())
        ttl = max(remaining, 60)  # at least 60s
    else:
        ttl = 3600

    from core.redis_store import set_add
    await set_add(_REVOKED_TOKENS_KEY, jti, ttl=ttl)
    return True


async def is_token_revoked(token: str) -> bool:
    """Check if a JWT has been revoked. Returns False if Redis unavailable."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return False

    jti = payload.get("jti")
    if not jti:
        return False  # tokens without jti can't be revoked

    from core.redis_store import set_check
    return await set_check(_REVOKED_TOKENS_KEY, jti)


def decode_token(token: str) -> dict | None:
    """Decode and validate a JWT. Returns the payload dict or None if invalid.

    Note: This is synchronous and does NOT check revocation. For revocation-aware
    validation, use decode_token_with_revocation_check() instead, or check
    is_token_revoked() separately in async contexts.
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return payload
    except JWTError:
        return None


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: str) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User | None:
    """Verify credentials and return the user, or None."""
    user = await get_user_by_email(db, email)
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user
