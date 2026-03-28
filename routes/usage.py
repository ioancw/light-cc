"""Usage tracking API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from core.usage import get_user_usage_summary, get_user_usage_by_model
from routes.auth import get_current_user, User

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("/summary")
async def usage_summary(user: User = Depends(get_current_user)):
    """Get total usage summary for the current user."""
    return await get_user_usage_summary(user.id)


@router.get("/by-model")
async def usage_by_model(user: User = Depends(get_current_user)):
    """Get usage breakdown by model for the current user."""
    return await get_user_usage_by_model(user.id)
