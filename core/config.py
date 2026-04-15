"""Load config.yaml + .env and expose typed settings."""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

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
    agents_dirs: list[str] = Field(default_factory=lambda: ["agents"])
    data_dir: str = "data"
    memory_dir: str = "data/users"


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    frontend: str = "classic"  # "classic" or "svelte"
    allowed_origins: list[str] = Field(default_factory=lambda: ["*"])


class ProviderConfig(BaseModel):
    """Configuration for an external model provider."""
    name: str  # e.g., "openai", "ollama"
    api_key_env: str | None = None  # env var name for the API key
    base_url: str | None = None  # custom base URL (e.g., for Ollama)
    models: list[str] = Field(default_factory=list)  # model names this provider handles


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
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-6",
    ])
    # Project directory for CLAUDE.md and .claude/rules/ discovery
    project_dir: str | None = None  # defaults to CWD at runtime
    # Hooks: event_name -> list of hook definitions
    hooks: dict[str, list[dict]] = Field(default_factory=dict)
    # Object storage (None = local filesystem)
    s3_bucket: str | None = Field(default_factory=lambda: os.environ.get("S3_BUCKET"))
    s3_region: str = Field(default_factory=lambda: os.environ.get("S3_REGION", "us-east-1"))
    s3_prefix: str = "lightcc/"
    tavily_api_key: str | None = Field(default_factory=lambda: os.environ.get("TAVILY_API_KEY"))
    providers: list[ProviderConfig] = Field(default_factory=list)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    # Suggestion chips shown on the new-chat empty state.
    # Each entry: {"label": "display text", "prompt": "actual message sent"}
    # If prompt starts with "/" it invokes the matching skill/command.
    # Model routing: classify input and direct to different models for cost/latency optimization.
    # Disabled by default. Each rule: {"pattern": "regex on user message", "model": "model-id"}
    # First match wins. No match = default model.
    routing_enabled: bool = False
    routing_rules: list[dict[str, str]] = Field(default_factory=lambda: [
        # Tier 1: Haiku -- greetings, simple questions, acknowledgements
        {"pattern": r"^(hello|hi|hey|thanks|thank you|good morning|good afternoon)\b", "model": "claude-haiku-4-5-20251001"},
        {"pattern": r"^(what time|what date|what day)\b", "model": "claude-haiku-4-5-20251001"},
        {"pattern": r"^(yes|no|ok|sure|got it|sounds good|perfect)\b", "model": "claude-haiku-4-5-20251001"},
        # Tier 3: Opus -- complex multi-step work, architecture, deep analysis
        {"pattern": r"(refactor|redesign|rearchitect|rewrite)\b", "model": "claude-opus-4-6"},
        {"pattern": r"(review|audit|evaluate|assess)\s+(the |this |my )?(code|codebase|architecture|system)", "model": "claude-opus-4-6"},
        {"pattern": r"(create|write|build|design)\s+(a |an )?(plan|architecture|strategy|spec)", "model": "claude-opus-4-6"},
        {"pattern": r"(research|compare|investigate|deep.?dive)", "model": "claude-opus-4-6"},
        {"pattern": r"(implement|build|create)\b.{0,40}(system|module|feature|service|api)\b", "model": "claude-opus-4-6"},
        # Tier 2: Sonnet -- everything else (default, no rule needed)
    ])
    suggestions: list[dict[str, str]] = Field(default_factory=lambda: [
        {"label": "Top business stories", "prompt": "/morning-briefing"},
        {"label": "Summarize a research paper", "prompt": "/analyze Upload or paste a research paper URL to summarize"},
        {"label": "Analyze a dataset", "prompt": "/analyze Upload a CSV to explore"},
        {"label": "What can you do?", "prompt": "What tools and skills do you have available? Give me a summary of your capabilities."},
    ])

    @model_validator(mode="after")
    def _check_jwt_secret(self) -> "Settings":
        if self.jwt_secret == "change-me-in-production":
            env = os.environ.get("ENV", "development").lower()
            if env in ("production", "prod", "staging"):
                raise ValueError(
                    "JWT_SECRET must be set to a secure value in production. "
                    "Set the JWT_SECRET environment variable."
                )
            warnings.warn(
                "Using default JWT_SECRET — set JWT_SECRET env var before deploying.",
                stacklevel=2,
            )
        return self


def load_settings() -> Settings:
    config_path = _PROJECT_ROOT / "config.yaml"
    if config_path.exists():
        raw: dict[str, Any] = yaml.safe_load(config_path.read_text()) or {}
        return Settings(**raw)
    return Settings()


settings = load_settings()
