"""Skill tool -- lets agents invoke a registered skill by name at runtime.

Mirrors Claude Code's `Skill` tool: an agent has its own inline system_prompt
(identity + behaviour), and separately can call Skill(skill="name", args="...")
during a run to pull a reusable procedure off the shelf. The tool resolves the
skill via the registry, runs argument substitution and dynamic-content
resolution, and returns the skill body as the tool result -- which the agent
then uses as guidance for its next turn.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from tools.registry import register_tool

logger = logging.getLogger(__name__)


async def handle_skill(tool_input: dict[str, Any]) -> str:
    """Resolve and return a skill body for the calling agent to follow."""
    skill_name = (tool_input.get("skill") or "").strip()
    args = tool_input.get("args", "") or ""

    if not skill_name:
        return json.dumps({"error": "skill name required"})

    from skills.registry import get_skill
    from core.models import resolve_dynamic_content
    from core.session import _current_session_id

    skill = get_skill(skill_name)
    if not skill:
        # Try stripping a leading slash in case the agent passed "/name"
        if skill_name.startswith("/"):
            skill = get_skill(skill_name[1:])
    if not skill:
        return json.dumps({"error": f"Unknown skill: {skill_name}"})

    if skill.disable_model_invocation:
        return json.dumps({
            "error": f"Skill '{skill_name}' cannot be invoked by the model.",
        })

    session_id = _current_session_id.get("") or ""
    try:
        body = skill.resolve_arguments(args, session_id=session_id)
        body = await resolve_dynamic_content(body)
    except Exception as e:
        logger.exception(f"Skill '{skill_name}' resolution failed")
        return json.dumps({"error": f"Skill resolution failed: {e}"})

    return body


register_tool(
    name="Skill",
    aliases=["skill"],
    description=(
        "Invoke a registered skill by name and receive its procedure text as guidance. "
        "Use this when the conversation's 'Available Skills' list includes a skill that matches the current task "
        "(e.g. a user asks for a 'morning briefing' and a `morning-briefing` skill is listed). "
        "The returned body is a runbook the model should then follow in its next turn. "
        "Pass the skill name (without a leading slash) and, optionally, a space-delimited `args` string "
        "that fills `$ARGUMENTS` / `$0` / `$1` placeholders inside the skill body. "
        "Returns an error JSON if the skill is unknown or not model-invocable."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "Registered skill name, e.g. 'morning-briefing'. No leading slash.",
            },
            "args": {
                "type": "string",
                "description": "Optional space-delimited arguments passed to the skill's $ARGUMENTS placeholders.",
            },
        },
        "required": ["skill"],
    },
    handler=handle_skill,
)
