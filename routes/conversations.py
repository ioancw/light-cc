"""Conversation CRUD endpoints."""

from __future__ import annotations

import re

from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy import select, update, or_

from core.database import get_db
from core.db_models import Conversation, Message
from core.search import search_conversations
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
    offset: int = Query(0, ge=0, description="Number of records to skip"),
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
        stmt = stmt.order_by(Conversation.updated_at.desc()).offset(offset).limit(limit)
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


@router.get("/search")
async def search_conversations_endpoint(
    q: str = Query("", description="Full-text search query"),
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
):
    if not q.strip():
        return []
    results = await search_conversations(user.id, q, limit=limit)
    return results


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
                Conversation.is_deleted == False,
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


@router.post("/{conversation_id}/fork")
async def fork_conversation_endpoint(conversation_id: str, user: User = Depends(get_current_user)):
    """Fork a conversation, creating a copy of all messages in a new conversation."""
    from core.session import fork_conversation

    try:
        new_conv_id, messages = await fork_conversation(conversation_id, user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Conversation not found or empty")

    return {"conversation_id": new_conv_id, "message_count": len(messages)}


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

    return {"status": "deleted"}


def _parse_claude_markdown(text: str) -> tuple[str, list[dict[str, str]]]:
    """Parse a Claude.ai markdown export into a title and message list.

    Supports formats:
      ## Human / ## Assistant
      ## User / ## Assistant
      **Human:** / **Assistant:**
      ### You / ### Claude (newer export format)
    """
    lines = text.split("\n")
    title = "Imported conversation"
    messages: list[dict[str, str]] = []

    # Extract title from first H1
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            title = stripped[2:].strip()
            break

    # Split on role headers
    role_pattern = re.compile(
        r"^(?:#{2,3}\s+(?:Human|User|You)|#{2,3}\s+(?:Assistant|Claude)"
        r"|\*\*(?:Human|User):\*\*|\*\*(?:Assistant|Claude):\*\*)\s*$",
        re.IGNORECASE,
    )

    current_role: str | None = None
    current_content: list[str] = []

    for line in lines:
        stripped = line.strip()
        if role_pattern.match(stripped):
            # Save previous block
            if current_role and current_content:
                content = "\n".join(current_content).strip()
                if content:
                    messages.append({"role": current_role, "content": content})
            # Determine new role
            lower = stripped.lower()
            if "human" in lower or "user" in lower or "you" in lower:
                current_role = "user"
            else:
                current_role = "assistant"
            current_content = []
        elif current_role is not None:
            current_content.append(line)

    # Don't forget the last block
    if current_role and current_content:
        content = "\n".join(current_content).strip()
        if content:
            messages.append({"role": current_role, "content": content})

    return title, messages


@router.post("/import")
async def import_conversation(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Import a markdown conversation export (e.g. from Claude.ai)."""
    content = await file.read()
    text = content.decode("utf-8", errors="replace")

    title, messages = _parse_claude_markdown(text)
    if not messages:
        # Not a conversation export — import as a single user message with the document content
        # Extract title from first H1 or use filename
        title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else (file.filename or "Imported document")
        messages = [{"role": "user", "content": text.strip()}]

    db = await get_db()
    try:
        conv = Conversation(user_id=user.id, title=title, model=None)
        db.add(conv)
        await db.flush()

        for msg in messages:
            db.add(Message(
                conversation_id=conv.id,
                role=msg["role"],
                content=msg["content"],
            ))

        await db.commit()
        conv_id = conv.id
    finally:
        await db.close()

    return {
        "conversation_id": conv_id,
        "title": title,
        "message_count": len(messages),
    }
