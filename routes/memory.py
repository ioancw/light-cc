"""Memory CRUD endpoints + per-user auto-extract settings.

The underlying storage layer lives in ``memory/manager.py`` -- these routes
are a thin REST wrapper so the frontend Memory panel can list, edit, and
delete entries (including provenance: user vs auto-extracted), and so the
user can toggle the auto-extraction feature.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import select

from fastapi import APIRouter, Depends, HTTPException, Query

from core.database import get_db
from core.db_models import Memory, User as UserModel
from memory.manager import (
    _MEMORY_TYPES as _VALID_MEMORY_TYPES,
    delete_memory,
    list_memories,
    save_memory,
    update_memory,
)
from routes.auth import get_current_user

router = APIRouter(prefix="/api", tags=["memory"])


# ── Schemas ──────────────────────────────────────────────────────────


class MemoryListItem(BaseModel):
    id: str
    title: str
    memory_type: str
    tags: list[str]
    source: str
    source_conversation_id: str | None


class MemoryDetail(BaseModel):
    id: str
    title: str
    content: str
    memory_type: str
    tags: list[str]
    source: str
    source_conversation_id: str | None
    created_at: str
    updated_at: str


class CreateMemoryRequest(BaseModel):
    title: str
    content: str
    memory_type: str = "note"
    tags: list[str] | None = None


class UpdateMemoryRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    memory_type: str | None = None
    tags: list[str] | None = None


class SettingsResponse(BaseModel):
    auto_extract_enabled: bool
    auto_extract_model: str
    auto_extract_min_messages: int


class UpdateSettingsRequest(BaseModel):
    auto_extract_enabled: bool | None = None
    auto_extract_model: str | None = None
    auto_extract_min_messages: int | None = Field(default=None, ge=1, le=100)


# ── Helpers ──────────────────────────────────────────────────────────

def _detail_from_row(m: Memory) -> MemoryDetail:
    return MemoryDetail(
        id=m.id,
        title=m.title,
        content=m.content,
        memory_type=m.memory_type,
        tags=m.tags_list,
        source=m.source,
        source_conversation_id=m.source_conversation_id,
        created_at=m.created_at.isoformat(),
        updated_at=m.updated_at.isoformat(),
    )


# ── Memory endpoints ─────────────────────────────────────────────────

@router.get("/memories", response_model=list[MemoryListItem])
async def api_list_memories(
    memory_type: str | None = Query(default=None),
    source: str | None = Query(default=None),
    user: UserModel = Depends(get_current_user),
):
    rows = await list_memories(user.id)
    # ``list_memories`` already filters by user; apply optional narrowing here
    # so the frontend can scope the view to e.g. auto-extracted entries only.
    if memory_type:
        rows = [r for r in rows if r.get("type") == memory_type]
    if source:
        rows = [r for r in rows if r.get("source") == source]
    return [
        MemoryListItem(
            id=r["id"],
            title=r["title"],
            memory_type=r["type"],
            tags=r.get("tags") or [],
            source=r.get("source") or "user",
            source_conversation_id=r.get("source_conversation_id"),
        )
        for r in rows
    ]


@router.post("/memories", response_model=MemoryDetail, status_code=201)
async def api_create_memory(
    req: CreateMemoryRequest, user: UserModel = Depends(get_current_user),
):
    if req.memory_type not in _VALID_MEMORY_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid memory_type. Must be one of {sorted(_VALID_MEMORY_TYPES)}",
        )
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="content cannot be empty")

    mem_id = await save_memory(
        user.id,
        req.title,
        req.content,
        tags=req.tags,
        memory_type=req.memory_type,
        source="user",
    )

    # Fetch the row back so we return the full detail (including timestamps).
    async with get_db() as db:
        row = (await db.execute(
            select(Memory).where(Memory.id == mem_id, Memory.user_id == user.id),
        )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=500, detail="Memory saved but could not be retrieved")
    return _detail_from_row(row)


@router.get("/memories/{memory_id}", response_model=MemoryDetail)
async def api_get_memory(
    memory_id: str, user: UserModel = Depends(get_current_user),
):
    async with get_db() as db:
        row = (await db.execute(
            select(Memory).where(Memory.id == memory_id, Memory.user_id == user.id),
        )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return _detail_from_row(row)


@router.patch("/memories/{memory_id}", response_model=MemoryDetail)
async def api_update_memory(
    memory_id: str,
    req: UpdateMemoryRequest,
    user: UserModel = Depends(get_current_user),
):
    kwargs = req.model_dump(exclude_unset=True)
    if not kwargs:
        raise HTTPException(status_code=400, detail="No fields to update")
    if "memory_type" in kwargs and kwargs["memory_type"] not in _VALID_MEMORY_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid memory_type. Must be one of {sorted(_VALID_MEMORY_TYPES)}",
        )

    ok = await update_memory(user.id, memory_id, **kwargs)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")

    async with get_db() as db:
        row = (await db.execute(
            select(Memory).where(Memory.id == memory_id, Memory.user_id == user.id),
        )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return _detail_from_row(row)


@router.delete("/memories/{memory_id}", status_code=204)
async def api_delete_memory(
    memory_id: str, user: UserModel = Depends(get_current_user),
):
    ok = await delete_memory(user.id, memory_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")


# ── User settings (auto-extract knobs) ───────────────────────────────

@router.get("/users/me/settings", response_model=SettingsResponse)
async def api_get_settings(user: UserModel = Depends(get_current_user)):
    return SettingsResponse(
        auto_extract_enabled=user.auto_extract_enabled,
        auto_extract_model=user.auto_extract_model,
        auto_extract_min_messages=user.auto_extract_min_messages,
    )


@router.patch("/users/me/settings", response_model=SettingsResponse)
async def api_update_settings(
    req: UpdateSettingsRequest, user: UserModel = Depends(get_current_user),
):
    kwargs = req.model_dump(exclude_unset=True)
    if not kwargs:
        raise HTTPException(status_code=400, detail="No fields to update")

    async with get_db() as db:
        row = (await db.execute(
            select(UserModel).where(UserModel.id == user.id),
        )).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        for k, v in kwargs.items():
            setattr(row, k, v)
        await db.commit()
        await db.refresh(row)
        return SettingsResponse(
            auto_extract_enabled=row.auto_extract_enabled,
            auto_extract_model=row.auto_extract_model,
            auto_extract_min_messages=row.auto_extract_min_messages,
        )
