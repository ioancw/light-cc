"""Pydantic v2 data models for Light CC."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolDef(BaseModel):
    """A tool definition in Claude API format."""

    name: str
    description: str
    input_schema: dict[str, Any]


class ToolResult(BaseModel):
    """Result from executing a tool."""

    tool_use_id: str
    content: str
    is_error: bool = False


class SkillDef(BaseModel):
    """A skill parsed from a SKILL.md file (Claude Code format).

    Skills are the primary abstraction in Claude Code/Cowork.
    They can be user-invoked (/name) and/or auto-invoked by Claude.
    """

    name: str
    description: str = ""
    argument_hint: str = ""  # Autocomplete hint, e.g. "[filename] [format]"
    tools: list[str] = Field(default_factory=list)  # allowed-tools
    disable_model_invocation: bool = False  # If true, only user can invoke via /name
    user_invocable: bool = True  # If false, hidden from / menu, only Claude can use
    model: str = ""  # Model override when this skill is active
    effort: str = ""  # Effort level: low, medium, high, max
    context: str = ""  # "fork" for subagent isolation
    agent: str = ""  # Subagent type when context=fork
    paths: list[str] = Field(default_factory=list)  # Glob patterns limiting activation
    prompt: str = ""  # The markdown body -- injected into system prompt

    def resolve_arguments(self, args: str) -> str:
        """Replace $ARGUMENTS in the prompt with the user's input."""
        return self.prompt.replace("$ARGUMENTS", args)


class CommandDef(BaseModel):
    """A slash command parsed from commands/*.md.

    Commands are user-invoked workflows that orchestrate multi-step processes.
    They typically reference skills in their body text for domain knowledge.
    Frontmatter: description and argument-hint.
    """

    name: str
    description: str = ""
    argument_hint: str = ""  # e.g. "[company name or ticker]"
    prompt: str = ""  # The markdown body -- workflow steps

    def resolve_arguments(self, args: str) -> str:
        """Replace $ARGUMENTS in the prompt with the user's input."""
        return self.prompt.replace("$ARGUMENTS", args)


class UserProfile(BaseModel):
    """Per-user profile and paths."""

    user_id: str
    data_dir: str
