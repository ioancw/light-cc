"""Full-text conversation search (LIKE-based, FTS can be added later)."""

from __future__ import annotations

from sqlalchemy import select, func, case

from core.database import get_db
from core.db_models import Conversation, Message


async def search_conversations(user_id: str, query: str, limit: int = 20) -> list[dict]:
    """Search message content across all conversations for a user.

    Returns a list of dicts with: conversation_id, title, snippet, created_at.
    Groups by conversation and returns the best match per conversation.
    """
    if not query or not query.strip():
        return []

    query = query.strip()
    pattern = f"%{query}%"

    async with get_db() as db:
        # Find messages matching the query in non-deleted conversations owned by user
        stmt = (
            select(
                Conversation.id.label("conversation_id"),
                Conversation.title,
                Message.content.label("snippet"),
                Message.created_at,
                # Rank: exact case match > case-insensitive match
                case(
                    (Message.content.like(pattern), 1),
                    else_=2,
                ).label("rank"),
            )
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Conversation.user_id == user_id,
                Conversation.is_deleted == False,
                Message.content.ilike(pattern),
            )
            .order_by("rank", Message.created_at.desc())
        )

        result = await db.execute(stmt)
        rows = result.all()

    # Group by conversation_id, keep best match per conversation
    seen: set[str] = set()
    results: list[dict] = []

    for row in rows:
        if row.conversation_id in seen:
            continue
        seen.add(row.conversation_id)

        # Extract a snippet around the match
        snippet = _extract_snippet(row.snippet, query, context_chars=80)

        results.append({
            "conversation_id": row.conversation_id,
            "title": row.title,
            "snippet": snippet,
            "created_at": row.created_at.isoformat(),
        })

        if len(results) >= limit:
            break

    return results


def _extract_snippet(content: str, query: str, context_chars: int = 80) -> str:
    """Extract a snippet of text around the first occurrence of query."""
    # Try to find the query case-insensitively
    lower_content = content.lower()
    lower_query = query.lower()
    idx = lower_content.find(lower_query)

    if idx == -1:
        # Shouldn't happen but fallback to start of content
        return content[:context_chars * 2] + ("..." if len(content) > context_chars * 2 else "")

    start = max(0, idx - context_chars)
    end = min(len(content), idx + len(query) + context_chars)

    snippet = content[start:end]

    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."

    return snippet
