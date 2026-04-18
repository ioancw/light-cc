"""API token management endpoints.

    POST   /api/tokens          -- create a new token (plaintext returned ONCE)
    GET    /api/tokens          -- list tokens (metadata only, no plaintext/hash)
    DELETE /api/tokens/{id}     -- revoke a token

Authenticated via the same ``get_current_user`` dependency that accepts either
a JWT access token or a previously-issued API token, so rotating an API token
from a browser session or from another API token both work.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException

from core.api_tokens import (
    create_api_token,
    list_api_tokens,
    revoke_api_token,
)
from core.db_models import User
from routes.auth import get_current_user

router = APIRouter(prefix="/api/tokens", tags=["api-tokens"])


class CreateTokenRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    expires_at: datetime | None = None


class TokenMetadata(BaseModel):
    """Token row with no secret material."""
    id: str
    name: str
    prefix: str
    created_at: str
    last_used_at: str | None
    expires_at: str | None
    revoked_at: str | None


class CreateTokenResponse(TokenMetadata):
    token: str  # plaintext, returned only on creation


def _to_metadata(row) -> TokenMetadata:
    return TokenMetadata(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        created_at=row.created_at.isoformat(),
        last_used_at=row.last_used_at.isoformat() if row.last_used_at else None,
        expires_at=row.expires_at.isoformat() if row.expires_at else None,
        revoked_at=row.revoked_at.isoformat() if row.revoked_at else None,
    )


@router.post("", response_model=CreateTokenResponse, status_code=201)
async def api_create_token(
    req: CreateTokenRequest, user: User = Depends(get_current_user),
):
    from core.rate_limit import check_rate_limit_async
    allowed, reason = await check_rate_limit_async(user.id, "token_create")
    if not allowed:
        raise HTTPException(status_code=429, detail=reason)
    try:
        row, plaintext = await create_api_token(
            user_id=user.id, name=req.name, expires_at=req.expires_at,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    meta = _to_metadata(row)
    return CreateTokenResponse(**meta.model_dump(), token=plaintext)


@router.get("", response_model=list[TokenMetadata])
async def api_list_tokens(user: User = Depends(get_current_user)):
    rows = await list_api_tokens(user.id)
    return [_to_metadata(r) for r in rows]


@router.delete("/{token_id}", status_code=204)
async def api_revoke_token(token_id: str, user: User = Depends(get_current_user)):
    revoked = await revoke_api_token(user.id, token_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Token not found or already revoked")
