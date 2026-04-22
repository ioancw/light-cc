"""File browser API — list, read, upload, and download files in user workspace."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.config import settings
from core.sandbox import get_workspace
from routes.auth import get_current_user, User

router = APIRouter(prefix="/api/files", tags=["files"])

# Download-URL signatures are short-lived JWTs with a dedicated type so
# a leaked access token can't masquerade as one (and vice versa).
_DOWNLOAD_TOKEN_TYPE = "file-download"
_UPLOAD_CHUNK = 64 * 1024


class FileEntry(BaseModel):
    name: str
    path: str  # Relative to workspace root
    is_dir: bool
    size: int | None = None


class FileContent(BaseModel):
    path: str
    content: str
    size: int


class DownloadURLRequest(BaseModel):
    path: str


class DownloadURLResponse(BaseModel):
    url: str
    expires_in: int


# ── Helpers ──────────────────────────────────────────────────────────

def _rel(workspace_root: Path, absolute: Path) -> str:
    """Return a forward-slash relative path from workspace root."""
    return absolute.relative_to(workspace_root).as_posix()


def _resolve_user_path(user: User, rel_path: str) -> tuple[Path, Path]:
    """Resolve a relative path within the user's workspace.

    Uses Path.relative_to for containment instead of a string prefix check —
    `startswith` is fooled by sibling directories (e.g. `workspace_evil`
    starts with `workspace`).
    """
    ws = get_workspace(user.id)
    if "\x00" in rel_path:
        raise HTTPException(status_code=400, detail="Invalid path")

    ws_root = ws.root.resolve()
    target = (ws.root / rel_path).resolve()
    try:
        target.relative_to(ws_root)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied: path outside workspace")
    return ws_root, target


def _extension_allowed(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    return ext not in {e.lower() for e in settings.files.blocked_extensions}


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


@router.post("/download-url", response_model=DownloadURLResponse)
async def create_download_url(
    req: DownloadURLRequest,
    user: User = Depends(get_current_user),
):
    """Mint a short-lived signed URL the browser can GET to download a file.

    Replaces the old `?token=<access JWT>` flow so the long-lived access token
    no longer lands in URLs, browser history, or server access logs.
    """
    ws_root, target = _resolve_user_path(user, req.path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    ttl = settings.files.download_url_ttl_seconds
    now = datetime.now(timezone.utc)
    payload = {
        "type": _DOWNLOAD_TOKEN_TYPE,
        "sub": user.id,
        "path": _rel(ws_root, target),
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
    }
    sig = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return DownloadURLResponse(
        url=f"/api/files/download?sig={sig}",
        expires_in=ttl,
    )


@router.get("/download")
async def download_file(
    sig: str = Query(..., description="Short-lived signed URL token (see POST /download-url)"),
):
    """Download a file using a signed URL token.

    Does not accept the long-lived access JWT — callers must first POST to
    /download-url to mint a scoped signature.
    """
    try:
        payload = jwt.decode(sig, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired download URL")
    if payload.get("type") != _DOWNLOAD_TOKEN_TYPE:
        raise HTTPException(status_code=401, detail="Wrong token type for download")

    user_id = payload.get("sub")
    rel_path = payload.get("path")
    if not user_id or not rel_path:
        raise HTTPException(status_code=401, detail="Malformed download URL")

    ws = get_workspace(user_id)
    ws_root = ws.root.resolve()
    target = (ws.root / rel_path).resolve()
    try:
        target.relative_to(ws_root)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # Force download + disable browser MIME sniffing so uploaded HTML/SVG
    # never runs in the site's origin.
    return FileResponse(
        target,
        filename=target.name,
        headers={
            "Content-Disposition": f'attachment; filename="{target.name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


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

    if not _extension_allowed(safe_name):
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed: {Path(safe_name).suffix}",
        )

    dest = (target_dir / safe_name).resolve()
    try:
        dest.relative_to(ws_root)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    max_bytes = settings.files.max_upload_mb * 1024 * 1024
    written = 0
    try:
        with open(dest, "wb") as f:
            while True:
                chunk = await file.read(_UPLOAD_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    f.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"Upload exceeds {settings.files.max_upload_mb} MB cap",
                    )
                f.write(chunk)
    except HTTPException:
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    return {
        "status": "ok",
        "path": _rel(ws_root, dest),
        "size": written,
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
