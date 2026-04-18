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
    # Modes:
    #   "off"   -- never route; always use settings.model
    #   "regex" -- pattern-match rules; first match wins (cheap, deterministic, dumb)
    #   "llm"   -- Haiku classifies into Haiku/Sonnet/Opus tiers; regex rules run first as a fast path
    # None means "unset" -- the validator honors the legacy routing_enabled flag
    # in that case. An explicit "off" disables routing even if routing_enabled=True.
    routing_mode: str | None = None
    # Legacy alias: if True and routing_mode is unset, implies routing_mode="regex".
    routing_enabled: bool = False
    # Model used as the LLM router when routing_mode="llm".
    routing_classifier_model: str = "claude-haiku-4-5-20251001"
    # Which model to dispatch to per tier label from the classifier.
    routing_tier_models: dict[str, str] = Field(default_factory=lambda: {
        "TRIVIAL":  "claude-haiku-4-5-20251001",
        "STANDARD": "claude-sonnet-4-6",
        "COMPLEX":  "claude-opus-4-6",
    })
    # Mirrors config.yaml defaults -- keep in sync. Only the Haiku fast path is
    # safe to regex-match. Greedy Opus patterns were removed because they pulled
    # ordinary requests into the most expensive tier; the llm classifier handles
    # tier selection when enabled.
    routing_rules: list[dict[str, str]] = Field(default_factory=lambda: [
        {"pattern": r"^(hello|hi|hey|thanks|thank you|good morning|good afternoon)\b", "model": "claude-haiku-4-5-20251001"},
        {"pattern": r"^(what time|what date|what day)\b", "model": "claude-haiku-4-5-20251001"},
        {"pattern": r"^(yes|no|ok|sure|got it|sounds good|perfect)\b", "model": "claude-haiku-4-5-20251001"},
    ])
    suggestions: list[dict[str, str]] = Field(default_factory=lambda: [
        {"label": "Top business stories", "prompt": "/morning-briefing"},
        {"label": "Summarize a research paper", "prompt": "/analyze Upload or paste a research paper URL to summarize"},
        {"label": "Analyze a dataset", "prompt": "/analyze Upload a CSV to explore"},
        {"label": "What can you do?", "prompt": "What tools and skills do you have available? Give me a summary of your capabilities."},
    ])

    @model_validator(mode="after")
    def _normalize_routing(self) -> "Settings":
        mode = (self.routing_mode or "").lower()
        if mode in ("off", "regex", "llm"):
            self.routing_mode = mode
            return self
        # Unset or unknown: honor legacy routing_enabled, else default off.
        self.routing_mode = "regex" if self.routing_enabled else "off"
        return self

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
    raw: dict[str, Any] = {}
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text()) or {}

    # Env vars ALWAYS win over config.yaml for secrets/infra so a deployed
    # container can override dev defaults without re-baking the image.
    for env_name, key in (
        ("DATABASE_URL", "database_url"),
        ("REDIS_URL", "redis_url"),
        ("JWT_SECRET", "jwt_secret"),
        ("TAVILY_API_KEY", "tavily_api_key"),
        ("S3_BUCKET", "s3_bucket"),
        ("S3_REGION", "s3_region"),
    ):
        val = os.environ.get(env_name)
        if val:
            raw[key] = val

    return Settings(**raw)


settings = load_settings()
