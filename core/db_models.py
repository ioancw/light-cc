"""SQLAlchemy ORM models for Light CC."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, Integer, UniqueConstraint, text as _sql_text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Auto-memory extraction (S3). Default off; opt-in per user.
    auto_extract_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=_sql_text("0"))
    auto_extract_model: Mapped[str] = mapped_column(String(100), default="claude-haiku-4-5-20251001", server_default="claude-haiku-4-5-20251001")
    auto_extract_min_messages: Mapped[int] = mapped_column(Integer, default=4, server_default=_sql_text("4"))

    conversations: Mapped[list[Conversation]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), default="New conversation")
    model: Mapped[str] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(back_populates="conversation", cascade="all, delete-orphan", order_by="(Message.created_at, Message.id)")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    conversation_id: Mapped[str] = mapped_column(String(32), ForeignKey("conversations.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" or "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)  # JSON-encoded content
    token_count: Mapped[int] = mapped_column(Integer, nullable=True)  # deprecated: not populated
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(32), nullable=True, index=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_schedule_user_name"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_timezone: Mapped[str] = mapped_column(String(50), default="UTC", server_default="UTC")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    runs: Mapped[list["ScheduleRun"]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan",
        order_by="ScheduleRun.started_at.desc()",
    )


class ScheduleRun(Base):
    __tablename__ = "schedule_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    schedule_id: Mapped[str] = mapped_column(String(32), ForeignKey("schedules.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    conversation_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("conversations.id"), nullable=True)

    schedule: Mapped["Schedule"] = relationship(back_populates="runs")


class Memory(Base):
    """Per-user persistent memory entries (Zettelkasten pattern)."""

    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    memory_type: Mapped[str] = mapped_column(String(50), default="note")
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    # Provenance (S3). "user" = explicitly saved via tool; "auto" = auto-extracted.
    source: Mapped[str] = mapped_column(String(20), default="user", server_default="user")
    source_conversation_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("conversations.id"), nullable=True, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    @property
    def tags_list(self) -> list[str]:
        """Parse tags JSON into a Python list."""
        if not self.tags:
            return []
        try:
            return json.loads(self.tags)
        except (json.JSONDecodeError, TypeError):
            return []

    @tags_list.setter
    def tags_list(self, value: list[str]) -> None:
        self.tags = json.dumps(value) if value else None


class AgentDefinition(Base):
    """Callable agent definition: a named persona with its own system prompt,
    tool filter, and model. Invoked via the ``Task`` tool from within a
    conversation, or via ``POST /api/agents/run`` headlessly.

    Agents do NOT have their own trigger/schedule -- that concern lives in
    ``Schedule`` rows whose prompt references the agent by name (e.g.
    ``/morning-briefing``). This mirrors Claude Code's model, where agents
    are purely callable and scheduling is a separate feature.
    """

    __tablename__ = "agent_definitions"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_agent_user_name"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)  # null = inherit default
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    tools: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of tool names (null = all)
    # JSON array of skill names the agent composes. When set, the "Available Skills"
    # section of the agent's system prompt is narrowed to this list, so the agent
    # only sees its declared skills (not every globally-registered one). Invocation
    # happens at runtime via the ``Skill`` tool. ``null`` = inherit all (legacy).
    skills: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_turns: Mapped[int] = mapped_column(Integer, default=20)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300)
    memory_scope: Mapped[str] = mapped_column(String(20), default="user")  # "user" | "agent" | "none"
    permissions: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(20), default="user")  # "user" | "yaml"
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan",
        order_by="AgentRun.started_at.desc()",
    )

    @property
    def tools_list(self) -> list[str] | None:
        if not self.tools:
            return None
        try:
            return json.loads(self.tools)
        except (json.JSONDecodeError, TypeError):
            return None

    @tools_list.setter
    def tools_list(self, value: list[str] | None) -> None:
        self.tools = json.dumps(value) if value else None

    @property
    def skills_list(self) -> list[str] | None:
        if not self.skills:
            return None
        try:
            return json.loads(self.skills)
        except (json.JSONDecodeError, TypeError):
            return None

    @skills_list.setter
    def skills_list(self, value: list[str] | None) -> None:
        self.skills = json.dumps(value) if value else None


class AgentRun(Base):
    """Execution record of an AgentDefinition."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    agent_id: Mapped[str] = mapped_column(String(32), ForeignKey("agent_definitions.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")  # running|completed|failed
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    conversation_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("conversations.id"), nullable=True)

    agent: Mapped["AgentDefinition"] = relationship(back_populates="runs")


class ApiToken(Base):
    """Long-lived personal access token for programmatic callers.

    Distinct from the short-lived JWT access tokens produced by ``/api/auth/login``:
    ApiTokens are opaque, revocable, optionally expiring, and intended for
    scripts, webhooks, and external integrations. Stored as a SHA-256 hash;
    the plaintext is returned to the caller exactly once, at creation.
    """

    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    """Audit log for tool executions (Phase 3 security)."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
