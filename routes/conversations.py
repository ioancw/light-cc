"""Conversation CRUD endpoints."""

from __future__ import annotations

from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update, or_

from core.database import get_db
from core.db_models import Conversation, Message
from routes.auth import get_current_user, User

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


# ── Response schemas ──────────────────────────────────────────────────

class ConversationSummary(BaseModel):
    id: str
    title: str
    model: str | None
    created_at: str
    updated_at: str


class ConversationDetail(BaseModel):
    id: str
    title: str
    model: str | None
    created_at: str
    updated_at: str
    messages: list[dict]


class UpdateConversationRequest(BaseModel):
    title: str | None = None
    model: str | None = None


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    q: str | None = Query(None, description="Search conversations by title"),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
):
    db = await get_db()
    try:
        stmt = (
            select(Conversation)
            .where(Conversation.user_id == user.id, Conversation.is_deleted == False)
        )
        if q:
            stmt = stmt.where(Conversation.title.ilike(f"%{q}%"))
        stmt = stmt.order_by(Conversation.updated_at.desc()).limit(limit)
        result = await db.execute(stmt)
        rows = result.scalars().all()
    finally:
        await db.close()

    return [
        ConversationSummary(
            id=c.id,
            title=c.title,
            model=c.model,
            created_at=c.created_at.isoformat(),
            updated_at=c.updated_at.isoformat(),
        )
        for c in rows
    ]


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: str, user: User = Depends(get_current_user)):
    db = await get_db()
    try:
        result = await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user.id,
                Conversation.is_deleted == False,
            )
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

        msg_result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )
        messages = msg_result.scalars().all()
    finally:
        await db.close()

    import json
    msg_list = []
    for m in messages:
        content = m.content
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            pass
        msg_list.append({"role": m.role, "content": content})

    return ConversationDetail(
        id=conv.id,
        title=conv.title,
        model=conv.model,
        created_at=conv.created_at.isoformat(),
        updated_at=conv.updated_at.isoformat(),
        messages=msg_list,
    )


@router.patch("/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    req: UpdateConversationRequest,
    user: User = Depends(get_current_user),
):
    db = await get_db()
    try:
        result = await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user.id,
            )
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

        values = {}
        if req.title is not None:
            values["title"] = req.title
        if req.model is not None:
            values["model"] = req.model

        if values:
            await db.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(**values)
            )
            await db.commit()
    finally:
        await db.close()

    return {"status": "ok"}


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str, user: User = Depends(get_current_user)):
    db = await get_db()
    try:
        result = await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user.id,
            )
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

        await db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(is_deleted=True)
        )
        await db.commit()
    finally:
        await db.close()

    return {"status": "ok"}
