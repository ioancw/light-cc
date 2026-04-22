"""Admin-only API endpoints — user management, usage overview, config."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from core.database import get_db
from core.db_models import Conversation, User, UsageEvent
from routes.auth import get_current_user, User as UserModel

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ── Admin gate ───────────────────────────────────────────────────────

async def require_admin(user: UserModel = Depends(get_current_user)) -> UserModel:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ── Users ────────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(admin: UserModel = Depends(require_admin)):
    """List all registered users with basic stats."""
    async with get_db() as db:
        result = await db.execute(
            select(
                User.id,
                User.email,
                User.display_name,
                User.is_admin,
                User.created_at,
                func.count(Conversation.id).label("conversation_count"),
            )
            .outerjoin(Conversation, (Conversation.user_id == User.id) & (Conversation.is_deleted == False))
            .group_by(User.id)
            .order_by(User.created_at.desc())
        )
        rows = result.all()

    return [
        {
            "id": r.id,
            "email": r.email,
            "display_name": r.display_name,
            "is_admin": r.is_admin,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "conversation_count": r.conversation_count,
        }
        for r in rows
    ]


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    is_admin: bool | None = None,
    admin: UserModel = Depends(require_admin),
):
    """Update user properties (currently: toggle admin status)."""
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot modify your own admin status")

    from sqlalchemy import update
    async with get_db() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        values = {}
        if is_admin is not None:
            values["is_admin"] = is_admin

        if values:
            await db.execute(update(User).where(User.id == user_id).values(**values))
            await db.commit()

    return {"status": "ok"}


# ── Usage overview ───────────────────────────────────────────────────

@router.get("/usage")
async def usage_overview(admin: UserModel = Depends(require_admin)):
    """Aggregate usage stats across all users."""
    async with get_db() as db:
        # Total stats
        total_result = await db.execute(
            select(
                func.sum(UsageEvent.input_tokens).label("total_input"),
                func.sum(UsageEvent.output_tokens).label("total_output"),
                func.sum(UsageEvent.cost_usd).label("total_cost"),
                func.count(UsageEvent.id).label("total_requests"),
                func.count(func.distinct(UsageEvent.user_id)).label("active_users"),
            )
        )
        totals = total_result.one()

        # Per-user breakdown
        per_user_result = await db.execute(
            select(
                User.email,
                User.display_name,
                func.sum(UsageEvent.input_tokens).label("input_tokens"),
                func.sum(UsageEvent.output_tokens).label("output_tokens"),
                func.sum(UsageEvent.cost_usd).label("cost_usd"),
                func.count(UsageEvent.id).label("requests"),
            )
            .join(User, User.id == UsageEvent.user_id)
            .group_by(User.id)
            .order_by(func.sum(UsageEvent.cost_usd).desc())
        )
        per_user = per_user_result.all()

        # Per-model breakdown
        per_model_result = await db.execute(
            select(
                UsageEvent.model,
                func.sum(UsageEvent.input_tokens).label("input_tokens"),
                func.sum(UsageEvent.output_tokens).label("output_tokens"),
                func.sum(UsageEvent.cost_usd).label("cost_usd"),
                func.count(UsageEvent.id).label("requests"),
            )
            .group_by(UsageEvent.model)
            .order_by(func.sum(UsageEvent.cost_usd).desc())
        )
        per_model = per_model_result.all()

    return {
        "totals": {
            "input_tokens": totals.total_input or 0,
            "output_tokens": totals.total_output or 0,
            "cost_usd": round(totals.total_cost or 0.0, 4),
            "requests": totals.total_requests or 0,
            "active_users": totals.active_users or 0,
        },
        "by_user": [
            {
                "email": r.email,
                "display_name": r.display_name,
                "input_tokens": r.input_tokens or 0,
                "output_tokens": r.output_tokens or 0,
                "cost_usd": round(r.cost_usd or 0.0, 4),
                "requests": r.requests or 0,
            }
            for r in per_user
        ],
        "by_model": [
            {
                "model": r.model,
                "input_tokens": r.input_tokens or 0,
                "output_tokens": r.output_tokens or 0,
                "cost_usd": round(r.cost_usd or 0.0, 4),
                "requests": r.requests or 0,
            }
            for r in per_model
        ],
    }
