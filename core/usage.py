"""Token usage tracking and cost estimation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.database import get_db
from core.db_models import UsageEvent

logger = logging.getLogger(__name__)

# Approximate pricing per 1M tokens (as of 2025)
_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6-20250514": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
    "claude-opus-4-0-20250514": {"input": 15.0, "output": 75.0},
}

_DEFAULT_PRICING = {"input": 3.0, "output": 15.0}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD for a given usage."""
    pricing = _PRICING.get(model, _DEFAULT_PRICING)
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


async def record_usage(
    user_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    conversation_id: str | None = None,
) -> None:
    """Record a usage event to the database."""
    if not user_id or user_id == "default":
        return

    cost = estimate_cost(model, input_tokens, output_tokens)
    db = await get_db()
    try:
        event = UsageEvent(
            user_id=user_id,
            conversation_id=conversation_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
        db.add(event)
        await db.commit()
    except Exception as e:
        logger.error(f"Failed to record usage: {e}")
        await db.rollback()
    finally:
        await db.close()


async def get_user_usage_summary(user_id: str) -> dict:
    """Get usage summary for a user."""
    from sqlalchemy import func, select

    db = await get_db()
    try:
        result = await db.execute(
            select(
                func.sum(UsageEvent.input_tokens).label("total_input"),
                func.sum(UsageEvent.output_tokens).label("total_output"),
                func.sum(UsageEvent.cost_usd).label("total_cost"),
                func.count(UsageEvent.id).label("total_requests"),
            ).where(UsageEvent.user_id == user_id)
        )
        row = result.one()
        return {
            "total_input_tokens": row.total_input or 0,
            "total_output_tokens": row.total_output or 0,
            "total_cost_usd": round(row.total_cost or 0.0, 6),
            "total_requests": row.total_requests or 0,
        }
    finally:
        await db.close()


async def get_user_usage_by_model(user_id: str) -> list[dict]:
    """Get usage breakdown by model for a user."""
    from sqlalchemy import func, select

    db = await get_db()
    try:
        result = await db.execute(
            select(
                UsageEvent.model,
                func.sum(UsageEvent.input_tokens).label("input_tokens"),
                func.sum(UsageEvent.output_tokens).label("output_tokens"),
                func.sum(UsageEvent.cost_usd).label("cost_usd"),
                func.count(UsageEvent.id).label("requests"),
            )
            .where(UsageEvent.user_id == user_id)
            .group_by(UsageEvent.model)
            .order_by(func.sum(UsageEvent.cost_usd).desc())
        )
        rows = result.all()
        return [
            {
                "model": r.model,
                "input_tokens": r.input_tokens or 0,
                "output_tokens": r.output_tokens or 0,
                "cost_usd": round(r.cost_usd or 0.0, 6),
                "requests": r.requests or 0,
            }
            for r in rows
        ]
    finally:
        await db.close()
