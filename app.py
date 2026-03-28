"""Chainlit entry point for Light CC.

Run with: chainlit run app.py
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import chainlit as cl

from core import agent
from core.config import settings
from tools.registry import get_all_tool_schemas, get_tool_schemas

import tools  # noqa: F401 — triggers tool registration

from skills.registry import load_skills, match_skill, list_skills
from memory.manager import load_memory, set_current_user, ensure_user_dirs
from core.permissions import is_blocked, is_risky, summarize_tool_call

logger = logging.getLogger(__name__)

# Load skills from all configured directories at startup
_PROJECT_ROOT = Path(__file__).resolve().parent
for skills_dir in settings.paths.skills_dirs:
    resolved = Path(skills_dir).expanduser()
    if not resolved.is_absolute():
        resolved = _PROJECT_ROOT / resolved
    load_skills(resolved)

import platform as _platform
import sys as _sys

_os_info = f"{_platform.system()} {_platform.release()}"
_python_info = _sys.executable
_outputs_dir = _PROJECT_ROOT / "data" / "outputs"
_outputs_dir.mkdir(parents=True, exist_ok=True)

BASE_SYSTEM_PROMPT = f"""You are Light CC, a helpful AI assistant with tools for data processing, \
visualization, and general tasks.

Environment: {_os_info} | Python: {_python_info}
Output directory: {_outputs_dir} (always use this for saving generated files)

Guidelines:
- For Python code, prefer the python_exec tool — it runs scripts as .py files and avoids \
shell quoting issues.
- For charts from generated data (e.g., math functions), use create_chart with x_values/y_values \
arrays, or python_exec with matplotlib/plotly.
- For charts from file data, load with load_data first, then use create_chart.
- The UI is dark-themed. Always use template='plotly_dark' for Plotly charts and dark styles \
for matplotlib (plt.style.use('dark_background')).
- The UI auto-renders images (PNG, JPG, etc.) from tool output — print file paths to stdout. \
Don't re-read or re-display files you just created.
- Always save output files to the output directory above. Never use /tmp/ or guess user directories.
- Keep responses concise unless the user asks for detail.
- Keep a professional tone. Do not use emojis in responses.

Model: {settings.model}
"""


def _build_system_prompt(
    skill_prompt: str | None = None,
    memory_context: str | None = None,
) -> str:
    """Build the full system prompt, optionally with a skill injection and memory."""
    parts = [BASE_SYSTEM_PROMPT]
    if skill_prompt:
        parts.append(f"\n## Active Skill\n{skill_prompt}")
    if memory_context:
        parts.append(f"\n## Your Memory\nThe following are things you remember about this user:\n{memory_context}")

    # List available skills
    skills = list_skills()
    if skills:
        skill_list = "\n".join(f"- /{s.name}: {s.description}" for s in skills)
        parts.append(f"\n## Available Skills\nUsers can activate skills with /command:\n{skill_list}")

    return "\n".join(parts)


def _get_user_id() -> str:
    """Get current user ID from Chainlit session."""
    user = cl.user_session.get("user")
    if user and hasattr(user, "identifier"):
        return user.identifier
    return "default"


@cl.on_chat_start
async def on_start() -> None:
    """Initialize session state. Only send welcome on first load."""
    user_id = _get_user_id()
    ensure_user_dirs(user_id)
    set_current_user(user_id)

    # Only send welcome if this is a fresh session (no existing messages)
    if cl.user_session.get("messages") is not None:
        return

    cl.user_session.set("messages", [])
    cl.user_session.set("datasets", {})
    cl.user_session.set("last_figure", None)

    skills = list_skills()
    welcome = "Ready. What would you like to do?"
    if skills:
        skill_list = "\n".join(f"- `/{s.name}` — {s.description}" for s in skills)
        welcome += f"\n\nAvailable skills:\n{skill_list}"

    await cl.Message(content=welcome).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    """Handle user message — run the agentic loop."""
    messages: list[dict[str, Any]] = cl.user_session.get("messages") or []

    # Add user message
    messages.append({"role": "user", "content": message.content})

    # Set user context for memory tools
    user_id = _get_user_id()
    set_current_user(user_id)

    # Ensure session-scoped stores are initialized
    if cl.user_session.get("datasets") is None:
        cl.user_session.set("datasets", {})
    if cl.user_session.get("last_figure") is None:
        cl.user_session.set("last_figure", None)

    # Load memory for this user
    memory_context = load_memory(user_id)

    # Check for skill activation
    skill = match_skill(message.content)
    skill_prompt = None
    tool_schemas = get_all_tool_schemas()

    if skill:
        # Extract arguments from /command (everything after the command name)
        msg_stripped = message.content.strip()
        if msg_stripped.startswith("/"):
            parts = msg_stripped.split(None, 1)
            args = parts[1] if len(parts) > 1 else ""
            skill_prompt = skill.resolve_arguments(args)
        else:
            skill_prompt = skill.prompt
        # Filter tools if skill specifies a subset
        if skill.tools:
            filtered = get_tool_schemas(skill.tools)
            if filtered:
                tool_schemas = filtered

        # Show skill activation in UI
        async with cl.Step(name=f"Activating skill: {skill.name}", type="tool") as step:
            step.output = skill.description
            await step.update()

    system = _build_system_prompt(skill_prompt, memory_context or None)

    # Response message — created lazily when first text arrives
    response_msg: cl.Message | None = None

    async def on_text(text: str) -> None:
        nonlocal response_msg
        if response_msg is None:
            response_msg = cl.Message(content="")
            await response_msg.send()
        await response_msg.stream_token(text)

    async def on_tool_start(name: str, tool_input: dict[str, Any]) -> cl.Step:
        step = cl.Step(name=name, type="tool")
        step.input = json.dumps(tool_input, indent=2)
        await step.send()
        return step

    _IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}

    async def on_tool_end(step: cl.Step, result: str) -> None:
        step.output = result
        await step.update()

        # Render Plotly charts inline
        from tools.registry import resolve_tool_name
        if resolve_tool_name(step.name or "") == "CreateChart":
            try:
                parsed = json.loads(result)
                if parsed.get("inline"):
                    from tools.chart import get_last_figure

                    fig = get_last_figure()
                    if fig is not None:
                        chart_el = cl.Plotly(
                            name=parsed.get("title", "Chart"),
                            figure=fig,
                        )
                        await chart_el.send(for_id=step.id)
            except (json.JSONDecodeError, ImportError):
                pass

        # Auto-render images from bash / python_exec stdout
        if resolve_tool_name(step.name or "") in ("Bash", "PythonExec"):
            try:
                parsed = json.loads(result)
                stdout = parsed.get("stdout", "")
                for line in stdout.strip().splitlines():
                    line = line.strip()
                    p = Path(line)
                    if p.suffix.lower() in _IMAGE_EXTS and p.exists():
                        img = cl.Image(name=p.stem, path=str(p), display="inline")
                        await img.send(for_id=step.id)
            except (json.JSONDecodeError, ValueError):
                pass

    async def on_permission_check(name: str, tool_input: dict[str, Any]) -> bool | str:
        """Check permissions before tool execution."""
        if is_blocked(name, tool_input):
            return "BLOCKED: This command is not allowed for safety reasons."
        if is_risky(name, tool_input):
            summary = summarize_tool_call(name, tool_input)
            res = await cl.AskActionMessage(
                content=f"**{name}** wants to run: {summary}",
                actions=[
                    cl.Action(name="allow", payload={"allow": True}, label="Allow"),
                    cl.Action(name="deny", payload={"allow": False}, label="Deny"),
                ],
            ).send()
            if res and res.get("payload", {}).get("allow"):
                return True
            return "User denied this action"
        return True

    # Run the agentic loop
    messages = await agent.run(
        messages=messages,
        tools=tool_schemas,
        system=system,
        on_text=on_text,
        on_tool_start=on_tool_start,
        on_tool_end=on_tool_end,
        on_permission_check=on_permission_check,
    )

    # Finalize the streamed message (if any text was produced)
    if response_msg is not None:
        # Detect local image paths in the response and attach as Chainlit elements
        img_pattern = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
        elements = []
        for match in img_pattern.finditer(response_msg.content or ""):
            alt_text, img_path = match.group(1), match.group(2)
            p = Path(img_path)
            if p.exists() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
                elements.append(cl.Image(name=alt_text or p.stem, path=str(p), display="inline"))
                # Remove the markdown image reference since cl.Image will render it
                response_msg.content = response_msg.content.replace(match.group(0), "")

        if elements:
            response_msg.elements = elements

        await response_msg.update()

    # Save messages back to session
    cl.user_session.set("messages", messages)
