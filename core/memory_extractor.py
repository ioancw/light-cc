"""Auto-extract memories from finished conversations (S3).

The extractor is off by default. It runs only when the user has set
``User.auto_extract_enabled``. It's deliberately lightweight:

    - Load the conversation messages + user settings.
    - Load the existing memory titles so we can skip duplicates.
    - Call the configured model (Haiku by default) with a fixed prompt
      that asks for a small JSON array of candidate memories.
    - Parse the JSON, drop malformed or duplicate items, and persist
      each survivor via ``save_memory(source="auto", ...)``.
    - Record an AuditEvent for provenance.

The call is intentionally non-streaming — we want one JSON blob back.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import select

from core.client import get_client
from core.database import get_db
from core.db_models import AuditEvent, Conversation, Memory, Message, User
from core.job_queue import register_job
from memory.manager import save_memory

logger = logging.getLogger(__name__)

# Default Haiku model for the extractor. Can be overridden per-user.
_DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# Memory types the extractor is allowed to emit. This is a deliberate
# subset of ``memory.manager._MEMORY_TYPES``: "feedback" and "user" are
# meta-categories that users create manually via the Memory panel, not
# things we auto-extract. Unknown types fall back to "note".
_ALLOWED_TYPES = {"note", "fact", "preference", "project", "reference"}

_EXTRACTION_SYSTEM_PROMPT = """You extract durable, reusable memories from a chat transcript.

Return ONLY a JSON array. No prose, no markdown fences. Schema per item:
  { "title": str, "content": str, "memory_type": one-of {note, fact, preference, project, reference}, "tags": [str, ...] }

Rules:
  - Emit AT MOST 5 items.
  - Skip anything already present in the "existing memory titles" list.
  - Skip small talk, one-off task details, and anything transient.
  - Prefer stable preferences, concrete facts about the user or their project,
    and references to external systems. Avoid restating obvious context.
  - If nothing is worth saving, return [].
"""


def _extract_plain_text(message_content: str) -> str:
    """Pull plain text out of a Message row's JSON content field."""
    if not message_content:
        return ""
    try:
        parsed = json.loads(message_content)
    except (json.JSONDecodeError, TypeError):
        return message_content
    if isinstance(parsed, str):
        return parsed
    if isinstance(parsed, list):
        parts: list[str] = []
        for block in parsed:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(p for p in parts if p)
    if isinstance(parsed, dict):
        return parsed.get("text", "") or str(parsed)
    return str(parsed)


def _parse_model_output(raw: str) -> list[dict[str, Any]]:
    """Parse the model's JSON output, tolerating minor garbage around it."""
    if not raw:
        return []
    # Strip common code fences.
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    # Find the outermost JSON array if the model leaked a little prose.
    match = re.search(r"\[.*\]", stripped, flags=re.DOTALL)
    if match:
        stripped = match.group(0)
    try:
        data = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _validate_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Return a cleaned record, or None if the shape is unusable."""
    title = str(item.get("title", "")).strip()
    content = str(item.get("content", "")).strip()
    if not title or not content:
        return None
    mtype = str(item.get("memory_type", "note")).strip().lower()
    if mtype not in _ALLOWED_TYPES:
        mtype = "note"
    raw_tags = item.get("tags") or []
    if not isinstance(raw_tags, list):
        raw_tags = []
    tags = [str(t).strip() for t in raw_tags if str(t).strip()]
    return {"title": title, "content": content, "memory_type": mtype, "tags": tags}


def _build_user_prompt(
    transcript: str, existing_titles: list[str],
) -> str:
    titles_block = (
        "\n".join(f"- {t}" for t in existing_titles) if existing_titles else "(none)"
    )
    return (
        f"Existing memory titles (do NOT duplicate):\n{titles_block}\n\n"
        f"Conversation transcript:\n---\n{transcript}\n---\n\n"
        "Return the JSON array now."
    )


async def _call_model(model: str, user_prompt: str) -> str:
    """Non-streaming call to the Anthropic client. Returns the text content."""
    client = get_client()
    resp = await client.messages.create(
        model=model,
        max_tokens=1024,
        system=_EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    # The SDK returns a Message with a content list of blocks. We want the
    # concatenated text. Tests use a MagicMock that sets resp.content[0].text.
    blocks = getattr(resp, "content", None) or []
    parts: list[str] = []
    for block in blocks:
        if getattr(block, "type", "text") == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts)


async def extract_memories_from_conversation(
    conversation_id: str,
    user_id: str,
    **_kwargs: Any,
) -> int:
    """Extract candidate memories from one conversation.

    Returns the number of memories actually persisted. Silent on all
    failure paths (logs at debug level) — this runs in the background
    and must never take down a request.
    """
    db = await get_db()
    try:
        user = (await db.execute(
            select(User).where(User.id == user_id)
        )).scalar_one_or_none()
        if user is None or not user.auto_extract_enabled:
            return 0

        conv = (await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
        )).scalar_one_or_none()
        if conv is None:
            return 0

        msg_rows = list((await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )).scalars().all())

        min_msgs = max(1, int(user.auto_extract_min_messages or 4))
        if len(msg_rows) < min_msgs:
            return 0

        existing_rows = list((await db.execute(
            select(Memory.title).where(Memory.user_id == user_id)
        )).scalars().all())
        existing_titles = [t for t in existing_rows if t]
        existing_lower = {t.lower() for t in existing_titles}

        model = user.auto_extract_model or _DEFAULT_MODEL

        transcript_lines: list[str] = []
        for m in msg_rows:
            text = _extract_plain_text(m.content)
            if text:
                transcript_lines.append(f"{m.role.upper()}: {text}")
        transcript = "\n\n".join(transcript_lines)
    finally:
        await db.close()

    if not transcript:
        return 0

    user_prompt = _build_user_prompt(transcript, existing_titles)

    try:
        raw = await _call_model(model, user_prompt)
    except Exception as e:
        logger.debug(f"auto-extract: model call failed: {e}")
        return 0

    items = _parse_model_output(raw)
    if not items:
        return 0

    saved = 0
    for item in items[:5]:  # hard cap
        cleaned = _validate_item(item)
        if not cleaned:
            continue
        if cleaned["title"].lower() in existing_lower:
            continue
        try:
            await save_memory(
                user_id,
                cleaned["title"],
                cleaned["content"],
                tags=cleaned["tags"],
                memory_type=cleaned["memory_type"],
                source="auto",
                source_conversation_id=conversation_id,
            )
            existing_lower.add(cleaned["title"].lower())
            saved += 1
        except Exception as e:
            logger.debug(f"auto-extract: save failed for '{cleaned['title']}': {e}")

    # AuditEvent for provenance
    if saved:
        try:
            db = await get_db()
            try:
                db.add(AuditEvent(
                    user_id=user_id,
                    tool_name="auto_memory_extract",
                    result_summary=f"saved={saved} from conversation={conversation_id}",
                    success=True,
                ))
                await db.commit()
            finally:
                await db.close()
        except Exception as e:
            logger.debug(f"auto-extract: audit log failed: {e}")

    return saved


# Register with the job queue so ``enqueue("extract_memories_from_conversation", ...)``
# picks it up in both arq and asyncio-fallback modes.
register_job("extract_memories_from_conversation", extract_memories_from_conversation)
