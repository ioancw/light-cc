"""Abstracted file storage — local filesystem (dev) or S3 (prod).

Usage:
    from core.storage import get_storage
    storage = get_storage()
    await storage.put("user123/outputs/chart.png", data)
    data = await storage.get("user123/outputs/chart.png")
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import AsyncIterator

from core.config import settings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Storage(ABC):
    """Abstract storage interface."""

    @abstractmethod
    async def put(self, key: str, data: bytes) -> None:
        """Store data at the given key."""

    @abstractmethod
    async def get(self, key: str) -> bytes | None:
        """Retrieve data by key, or None if not found."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete data at the given key."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if a key exists."""

    @abstractmethod
    async def list_keys(self, prefix: str = "") -> list[str]:
        """List all keys under a prefix."""

    @abstractmethod
    async def get_url(self, key: str, expires_in: int = 3600) -> str | None:
        """Get a URL for direct access (presigned URL for S3, file path for local)."""


class LocalStorage(Storage):
    """Local filesystem storage under data/ directory."""

    def __init__(self, base_dir: str | None = None) -> None:
        self.base = Path(base_dir) if base_dir else _PROJECT_ROOT / settings.paths.data_dir / "users"
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.base / key

    async def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def get(self, key: str) -> bytes | None:
        path = self._path(key)
        if not path.exists():
            return None
        return await asyncio.to_thread(path.read_bytes)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            await asyncio.to_thread(path.unlink)

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()

    async def list_keys(self, prefix: str = "") -> list[str]:
        base = self._path(prefix) if prefix else self.base
        if not base.exists():
            return []
        keys = []
        for p in base.rglob("*"):
            if p.is_file():
                keys.append(str(p.relative_to(self.base)).replace("\\", "/"))
        return keys

    async def get_url(self, key: str, expires_in: int = 3600) -> str | None:
        path = self._path(key)
        if path.exists():
            return str(path)
        return None


class S3Storage(Storage):
    """AWS S3 storage backend."""

    def __init__(self) -> None:
        import boto3
        self._s3 = boto3.client("s3", region_name=settings.s3_region)
        self._bucket = settings.s3_bucket
        self._prefix = settings.s3_prefix

    def _key(self, key: str) -> str:
        return self._prefix + key

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(
            self._s3.put_object,
            Bucket=self._bucket,
            Key=self._key(key),
            Body=data,
        )

    async def get(self, key: str) -> bytes | None:
        try:
            resp = await asyncio.to_thread(
                self._s3.get_object,
                Bucket=self._bucket,
                Key=self._key(key),
            )
            return resp["Body"].read()
        except self._s3.exceptions.NoSuchKey:
            return None

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(
            self._s3.delete_object,
            Bucket=self._bucket,
            Key=self._key(key),
        )

    async def exists(self, key: str) -> bool:
        try:
            await asyncio.to_thread(
                self._s3.head_object,
                Bucket=self._bucket,
                Key=self._key(key),
            )
            return True
        except Exception:
            return False

    async def list_keys(self, prefix: str = "") -> list[str]:
        full_prefix = self._key(prefix)
        resp = await asyncio.to_thread(
            self._s3.list_objects_v2,
            Bucket=self._bucket,
            Prefix=full_prefix,
        )
        keys = []
        for obj in resp.get("Contents", []):
            # Strip the global prefix to return relative keys
            rel = obj["Key"][len(self._prefix):]
            keys.append(rel)
        return keys

    async def get_url(self, key: str, expires_in: int = 3600) -> str | None:
        try:
            url = await asyncio.to_thread(
                self._s3.generate_presigned_url,
                "get_object",
                Params={"Bucket": self._bucket, "Key": self._key(key)},
                ExpiresIn=expires_in,
            )
            return url
        except Exception:
            return None


# ── Singleton factory ─────────────────────────────────────────────────

_instance: Storage | None = None


def get_storage() -> Storage:
    """Get the configured storage backend (lazy singleton)."""
    global _instance
    if _instance is None:
        if settings.s3_bucket:
            logger.info(f"Using S3 storage: {settings.s3_bucket}")
            _instance = S3Storage()
        else:
            logger.info("Using local filesystem storage")
            _instance = LocalStorage()
    return _instance
