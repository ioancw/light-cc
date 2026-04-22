"""Pydantic v2 data models for Light CC."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


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
    """A skill parsed from a SKILL.md file.

    Compliant with the agentskills.io open standard (https://agentskills.io/specification)
    plus Claude Code extensions (argument-hint, disable-model-invocation, etc.).

    Skills are the primary abstraction in Claude Code/Cowork.
    They can be user-invoked (/name) and/or auto-invoked by Claude.
    """

    # --- agentskills.io standard fields ---
    name: str
    description: str = ""
    license: str = ""  # License name or reference to bundled LICENSE file
    compatibility: str = ""  # Environment requirements (max 500 chars per spec)
    metadata: dict[str, str] = Field(default_factory=dict)  # Arbitrary key-value pairs
    tools: list[str] = Field(default_factory=list)  # allowed-tools (spec: space-delimited)

    # --- Claude Code extension fields ---
    argument_hint: str = ""  # Autocomplete hint, e.g. "[filename] [format]"
    disable_model_invocation: bool = False  # If true, only user can invoke via /name
    user_invocable: bool = True  # If false, hidden from / menu, only Claude can use
    model: str = ""  # Model override when this skill is active
    effort: str = ""  # Effort level: low, medium, high, max
    context: str = ""  # "fork" for subagent isolation
    agent: str = ""  # Subagent type when context=fork
    paths: list[str] = Field(default_factory=list)  # Glob patterns limiting activation
    skill_dir: str = ""  # Directory containing this skill's SKILL.md
    prompt: str = ""  # The markdown body -- injected into system prompt

    # Origin tag. ``"skill"`` is a real SKILL.md/skills directory entry;
    # ``"legacy-command"`` is a ``commands/*.md`` file loaded into the unified
    # registry (matches CC's semantic merge in 2.1+: same `/foo` surface,
    # two file locations). The plain user surface treats both the same -- this
    # field exists for back-compat reporting (e.g. ``list_commands()``).
    kind: str = "skill"

    def resolve_arguments(self, args: str, session_id: str = "") -> str:
        """Substitute variables in the prompt.

        Supports Claude Code's variable syntax:
        - $ARGUMENTS — full argument string
        - $ARGUMENTS[N] — Nth positional argument (0-based)
        - $N — shorthand for $ARGUMENTS[N]
        - ${CLAUDE_SESSION_ID} — current session ID
        - ${CLAUDE_SKILL_DIR} — directory containing the skill file
        If $ARGUMENTS is not referenced in the prompt, appends it.
        """
        result = self.prompt
        parts = args.split() if args else []

        # Positional: $ARGUMENTS[N]
        def _replace_indexed(m: re.Match) -> str:
            idx = int(m.group(1))
            return parts[idx] if idx < len(parts) else ""

        result = re.sub(r"\$ARGUMENTS\[(\d+)\]", _replace_indexed, result)

        # Full $ARGUMENTS replacement (track whether it was present)
        has_arguments_var = "$ARGUMENTS" in result
        result = result.replace("$ARGUMENTS", args)

        # Positional shorthand: $0, $1, etc. (bare — not inside another identifier)
        def _replace_positional(m: re.Match) -> str:
            idx = int(m.group(1))
            return parts[idx] if idx < len(parts) else ""

        result = re.sub(r"(?<!\w)\$(\d+)(?!\w)", _replace_positional, result)

        # Session and skill-dir variables
        result = result.replace("${CLAUDE_SESSION_ID}", session_id)
        result = result.replace("${CLAUDE_SKILL_DIR}", self.skill_dir)

        # If $ARGUMENTS was never in the original prompt, append args
        if not has_arguments_var and args:
            result = result.rstrip() + f"\n\nARGUMENTS: {args}"

        return result


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


_DYNAMIC_CMD_RE = re.compile(r"!`([^`]+)`")


async def resolve_dynamic_content(text: str, timeout: float = 30.0) -> str:
    """Preprocess !`command` placeholders in skill/command content.

    Runs each shell command and replaces the placeholder with its stdout.
    This matches Claude Code's dynamic context injection behavior.
    """
    matches = list(_DYNAMIC_CMD_RE.finditer(text))
    if not matches:
        return text

    result = text
    for m in reversed(matches):  # reverse to preserve positions
        cmd = m.group(1)
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode("utf-8", errors="replace").strip()
        except asyncio.TimeoutError:
            logger.warning(f"Dynamic content command timed out: {cmd}")
            output = f"[command timed out: {cmd}]"
        except Exception as e:
            logger.warning(f"Dynamic content command failed: {cmd}: {e}")
            output = f"[command failed: {cmd}]"
        result = result[:m.start()] + output + result[m.end():]

    return result


class UserProfile(BaseModel):
    """Per-user profile and paths."""

    user_id: str
    data_dir: str
