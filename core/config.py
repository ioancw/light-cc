"""Load config.yaml + .env and expose typed settings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(_PROJECT_ROOT / ".env")


class PathsConfig(BaseModel):
    skills_dirs: list[str] = Field(default_factory=lambda: [
        "skills",              # Light CC native
        ".claude/skills",      # CC project-level skills
        ".claude/commands",    # CC project-level commands (legacy, same abstraction)
        "~/.claude/skills",    # CC personal skills
    ])
    commands_dirs: list[str] = Field(default_factory=lambda: ["commands"])
    plugins_dirs: list[str] = Field(default_factory=lambda: ["plugins"])
    data_dir: str = "data"
    memory_dir: str = "data/users"


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class AuthConfig(BaseModel):
    registration_enabled: bool = True
    jwt_expiry_hours: int = 1
    jwt_refresh_expiry_days: int = 7


class Settings(BaseModel):
    model: str = "claude-sonnet-4-6-20250514"
    max_tokens: int = 4096
    max_context_tokens: int = 180000
    max_turns: int = 50
    compression_threshold: float = 0.8
    max_tool_result_chars: int = 50000
    python_path: str | None = None  # Python executable; falls back to sys.executable
    database_url: str = Field(default_factory=lambda: os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///data/lightcc.db"))
    redis_url: str | None = Field(default_factory=lambda: os.environ.get("REDIS_URL"))
    jwt_secret: str = Field(default_factory=lambda: os.environ.get("JWT_SECRET", "change-me-in-production"))
    jwt_algorithm: str = "HS256"
    available_models: list[str] = Field(default_factory=lambda: [
        "claude-sonnet-4-6-20250514",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-0-20250514",
    ])
    # Project directory for CLAUDE.md and .claude/rules/ discovery
    project_dir: str | None = None  # defaults to CWD at runtime
    # Hooks: event_name -> list of hook definitions
    hooks: dict[str, list[dict]] = Field(default_factory=dict)
    # Object storage (None = local filesystem)
    s3_bucket: str | None = Field(default_factory=lambda: os.environ.get("S3_BUCKET"))
    s3_region: str = Field(default_factory=lambda: os.environ.get("S3_REGION", "us-east-1"))
    s3_prefix: str = "lightcc/"
    paths: PathsConfig = Field(default_factory=PathsConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)


def load_settings() -> Settings:
    config_path = _PROJECT_ROOT / "config.yaml"
    if config_path.exists():
        raw: dict[str, Any] = yaml.safe_load(config_path.read_text()) or {}
        return Settings(**raw)
    return Settings()


settings = load_settings()
