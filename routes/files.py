"""File browser API — list, read, upload, and download files in user workspace."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.sandbox import get_workspace
from routes.auth import get_current_user, User

router = APIRouter(prefix="/api/files", tags=["files"])


class FileEntry(BaseModel):
    name: str
    path: str  # Relative to workspace root
    is_dir: bool
    size: int | None = None


class FileContent(BaseModel):
    path: str
    content: str
    size: int


# ── Helpers ──────────────────────────────────────────────────────────

def _rel(workspace_root: Path, absolute: Path) -> str:
    """Return a forward-slash relative path from workspace root."""
    return absolute.relative_to(workspace_root).as_posix()


def _resolve_user_path(user: User, rel_path: str) -> tuple[Path, Path]:
    """Resolve a relative path within the user's workspace.

    Returns (workspace_root, resolved_absolute_path).
    Raises HTTPException 403 on path traversal attempts.
    """
    ws = get_workspace(user.id)
    if "\x00" in rel_path:
        raise HTTPException(status_code=400, detail="Invalid path")

    target = (ws.root / rel_path).resolve()
    if not str(target).startswith(str(ws.root.resolve())):
        raise HTTPException(status_code=403, detail="Access denied: path outside workspace")
    return ws.root, target


# ── Endpoints ────────────────────────────────────────────────────────

@router.get("/list", response_model=list[FileEntry])
async def list_files(
    path: str = Query("", description="Relative directory path within workspace"),
    user: User = Depends(get_current_user),
):
    """List files and directories at the given path."""
    ws_root, target = _resolve_user_path(user, path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="Directory not found")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")

    entries = []
    for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if item.name.startswith("."):
            continue
        entries.append(FileEntry(
            name=item.name,
            path=_rel(ws_root, item),
            is_dir=item.is_dir(),
            size=item.stat().st_size if item.is_file() else None,
        ))
    return entries


@router.get("/read")
async def read_file(
    path: str = Query(..., description="Relative file path within workspace"),
    user: User = Depends(get_current_user),
):
    """Read a text file's contents."""
    ws_root, target = _resolve_user_path(user, path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Not a file")
    if target.stat().st_size > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 2MB)")

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Binary file — use download instead")

    return FileContent(
        path=_rel(ws_root, target),
        content=content,
        size=len(content),
    )


async def _get_user_from_token(token: str) -> User:
    """Resolve a JWT string to a User, or raise 401."""
    from core.auth import decode_token, get_user_by_id
    from core.database import get_db
    payload = decode_token(token)
    if payload is None or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token")
    db = await get_db()
    try:
        user = await get_user_by_id(db, payload["sub"])
    finally:
        await db.close()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user


@router.get("/download")
async def download_file(
    path: str = Query(..., description="Relative file path within workspace"),
    token: str = Query(..., description="JWT access token"),
):
    """Download a file."""
    user = await _get_user_from_token(token)
    _, target = _resolve_user_path(user, path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Not a file")

    return FileResponse(target, filename=target.name)


@router.post("/upload")
async def upload_file(
    path: str = Query("", description="Relative directory to upload into"),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Upload a file to the workspace."""
    ws_root, target_dir = _resolve_user_path(user, path)

    if not target_dir.exists():
        target_dir.mkdir(parents=True, exist_ok=True)

    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")

    # Sanitize filename
    safe_name = Path(file.filename).name
    if not safe_name or safe_name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")

    dest = target_dir / safe_name
    # Verify dest is still within workspace
    if not str(dest.resolve()).startswith(str(ws_root.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")

    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    return {
        "status": "ok",
        "path": _rel(ws_root, dest),
        "size": dest.stat().st_size,
    }


@router.delete("")
async def delete_file(
    path: str = Query(..., description="Relative path to delete"),
    user: User = Depends(get_current_user),
):
    """Delete a file or empty directory."""
    _, target = _resolve_user_path(user, path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")

    if target.is_dir():
        if any(target.iterdir()):
            raise HTTPException(status_code=400, detail="Directory not empty")
        target.rmdir()
    else:
        target.unlink()

    return {"status": "ok"}
