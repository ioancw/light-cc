"""Agent message handler -- processes user messages through the agentic loop.

Contains _handle_user_message and its supporting functions (title generation,
context summarization, agent callbacks).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from core import agent
from core.config import settings
from core.hooks import fire_hooks, has_hooks
from core.models import resolve_dynamic_content
from core.permissions import summarize_tool_call
from core.project_config import load_project_config
from core.rules import get_active_rules, load_rules
from core.session import (
    connection_get,
    connection_set,
    conv_session_get,
    conv_session_set,
    save_conversation,
    set_current_cid,
    set_current_session,
)
from commands.registry import get_command, list_commands
from handlers.commands import handle_plugin_command, handle_schedule_command
from handlers.media import send_chart_if_any, send_images_if_any, send_tables_if_any
from core.telemetry import async_trace_span, record_error
from memory.manager import ensure_user_dirs, load_memory, set_current_user
from skills.registry import list_skills, match_skill_by_intent, match_skill_by_name
from tools.registry import get_all_tool_schemas, get_tool_schemas

logger = logging.getLogger(__name__)

SendEvent = Callable[[str, dict[str, Any]], Awaitable[None]]


async def generate_title(messages: list[dict[str, Any]]) -> str:
    """Generate a short conversation title from the first few messages."""
    from core.client import get_client
    client = get_client()
    text_parts = []
    for m in messages[:4]:
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )
        if isinstance(content, str):
            text_parts.append(f"{m.get('role', 'unknown')}: {content[:200]}")
    prompt = (
        "Summarize this conversation in 4-6 words for a sidebar title. "
        "Return only the title, no quotes or punctuation.\n\n"
        + "\n".join(text_parts)
    )
    try:
        title_model = settings.title_model if hasattr(settings, "title_model") and settings.title_model else "claude-haiku-4-5-20251001"
        resp = await client.messages.create(
            model=title_model,
            max_tokens=30,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        logger.warning(f"Title generation failed: {e}")
        return ""


async def summarize_messages(messages: list[dict[str, Any]]) -> str:
    """Summarize older messages to reduce context size."""
    from core.client import get_client
    client = get_client()

    to_summarize = messages[:-4]
    text_parts = []
    for m in to_summarize:
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )
        if isinstance(content, str) and content.strip():
            role = m.get("role", "unknown")
            text_parts.append(f"{role}: {content[:500]}")

    combined = "\n".join(text_parts)[:8000]
    prompt = (
        "Summarize this conversation history concisely, preserving key facts, "
        "decisions, code snippets mentioned, and context needed to continue the conversation. "
        "Be thorough but compact.\n\n" + combined
    )
    resp = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


async def _list_user_agents(user_id: str) -> list[tuple[str, str]]:
    """Return (name, description) for this user's enabled AgentDefinitions.

    Surfaced in the system prompt so the model knows which specialists it can
    delegate to via the `Agent` tool. Returns [] on any failure -- a missing
    agents list should never break the chat path.
    """
    if not user_id or user_id == "default":
        return []
    try:
        from sqlalchemy import select
        from core.database import get_db
        from core.db_models import AgentDefinition

        db = await get_db()
        try:
            res = await db.execute(
                select(AgentDefinition.name, AgentDefinition.description).where(
                    AgentDefinition.user_id == user_id,
                    AgentDefinition.enabled.is_(True),
                )
            )
            return [(n, d) for n, d in res.all()]
        finally:
            await db.close()
    except Exception as e:
        logger.debug(f"_list_user_agents failed for {user_id}: {e}")
        return []


async def handle_user_message(
    session_id: str,
    cid: str,
    data: dict[str, Any],
    send_event: SendEvent,
    pending_permissions: dict[str, asyncio.Future],
    *,
    build_system_prompt,
    outputs_dir: Path,
) -> None:
    """Process a user message through the agentic loop."""
    set_current_session(session_id)
    set_current_cid(cid)
    user_id = connection_get(session_id, "user_id") or "default"
    set_current_user(user_id)
    ensure_user_dirs(user_id)

    messages = conv_session_get(cid, "messages")
    if messages is None:
        messages = []
        conv_session_set(cid, "messages", messages)

    # Fire PromptSubmit hooks
    if has_hooks("PromptSubmit"):
        await fire_hooks("PromptSubmit", {"text": data["text"], "user_id": user_id})

    messages.append({"role": "user", "content": data["text"]})

    memory_context = await load_memory(user_id)
    user_system_prompt = connection_get(session_id, "user_system_prompt") or ""

    # Load project config (CLAUDE.md) and rules -- cached per session
    project_config = connection_get(session_id, "project_config")
    if project_config is None:
        project_dir = Path(settings.project_dir) if settings.project_dir else Path.cwd()
        project_config = load_project_config(project_dir)
        connection_set(session_id, "project_config", project_config)
    project_rules = connection_get(session_id, "project_rules")
    if project_rules is None:
        project_dir = Path(settings.project_dir) if settings.project_dir else Path.cwd()
        project_rules = load_rules(project_dir)
        connection_set(session_id, "project_rules", project_rules)

    # Compute active rules based on files touched so far
    active_files: list[str] = conv_session_get(cid, "active_files") or []
    rules_text = get_active_rules(project_rules, active_files) if project_rules else ""

    # Set per-user output directory in the system prompt
    if user_id != "default":
        from core.sandbox import get_workspace
        workspace = get_workspace(user_id)
        user_outputs_dir = workspace.outputs
    else:
        user_outputs_dir = outputs_dir

    # Skill and command matching
    from core.session import set_active_tool_filter
    active_prompt = None
    tool_schemas = get_all_tool_schemas()
    set_active_tool_filter(None)

    msg_stripped = data["text"].strip()
    matched_skill = None
    matched_command = None

    if msg_stripped.startswith("/"):
        slash_name = msg_stripped.split()[0][1:]
        parts = msg_stripped.split(None, 1)
        args = parts[1] if len(parts) > 1 else ""

        # Built-in: /reload
        if slash_name == "reload":
            from skills.registry import reload_skills
            from commands.registry import reload_commands
            n_skills = reload_skills()
            n_cmds = reload_commands()
            # Clear cached project config/rules so they're re-read too
            connection_set(session_id, "project_config", None)
            connection_set(session_id, "project_rules", None)
            await send_event("text_delta", {"text": f"Reloaded {n_skills} skills and {n_cmds} commands."})
            # Notify frontend of updated skill list
            refreshed = [
                {"name": s.name, "description": s.description, "argument_hint": s.argument_hint}
                for s in list_skills() if s.user_invocable
            ] + [
                {"name": c.name, "description": c.description, "argument_hint": c.argument_hint}
                for c in list_commands()
            ] + [
                {"name": "context", "description": "Show context window usage breakdown", "argument_hint": ""},
                {"name": "plugin", "description": "Install, list, update, or uninstall plugins", "argument_hint": "install|list|update|uninstall <name-or-url>"},
                {"name": "schedule", "description": "Create, list, enable, disable, or delete scheduled agent tasks", "argument_hint": "create|list|enable|disable|delete|runs|run"},
                {"name": "reload", "description": "Reload all skills, commands, and project config from disk", "argument_hint": ""},
            ]
            await send_event("skills_updated", {"skills": refreshed})
            await send_event("turn_complete", {})
            return

        # Built-in: /context
        if slash_name == "context":
            from core.context import get_context_breakdown
            breakdown = await get_context_breakdown(
                messages, system="(pending)",
                tools=tool_schemas,
                project_config=connection_get(session_id, "project_config") or "",
                rules_text=get_active_rules(connection_get(session_id, "project_rules") or [], conv_session_get(cid, "active_files") or []),
                memory_context=await load_memory(user_id),
            )
            lines = [
                "**Context Window Usage**",
                f"- System prompt: ~{breakdown['system_prompt_tokens']:,} tokens",
                f"- Project config (CLAUDE.md): ~{breakdown['project_config_tokens']:,} tokens",
                f"- Project rules: ~{breakdown['rules_tokens']:,} tokens",
                f"- Memory: ~{breakdown['memory_tokens']:,} tokens",
                f"- Skills: ~{breakdown['skill_tokens']:,} tokens",
                f"- Tool schemas: ~{breakdown['tools_tokens']:,} tokens",
                f"- Messages: ~{breakdown['messages_tokens']:,} tokens",
                f"- **Total: ~{breakdown['total_tokens']:,} / {breakdown['max_tokens']:,} ({breakdown['usage_pct']}%)**",
            ]
            await send_event("text_delta", {"text": "\n".join(lines)})
            await send_event("response_end", {})
            return

        # Built-in: /plugin
        if slash_name == "plugin":
            result = await handle_plugin_command(args)
            await send_event("text_delta", {"text": result})
            sub = args.strip().split()[0].lower() if args.strip() else ""
            if sub in ("install", "uninstall", "remove", "update"):
                refreshed = [
                    {"name": s.name, "description": s.description, "argument_hint": s.argument_hint}
                    for s in list_skills() if s.user_invocable
                ] + [
                    {"name": c.name, "description": c.description, "argument_hint": c.argument_hint}
                    for c in list_commands()
                ] + [
                    {"name": "context", "description": "Show context window usage breakdown", "argument_hint": ""},
                    {"name": "plugin", "description": "Install, list, update, or uninstall plugins", "argument_hint": "install|list|update|uninstall <name-or-url>"},
                ]
                await send_event("skills_updated", {"skills": refreshed})
            await send_event("turn_complete", {})
            return

        # Built-in: /schedule
        if slash_name == "schedule":
            result = await handle_schedule_command(args, user_id)
            await send_event("text_delta", {"text": result})
            await send_event("turn_complete", {})
            return

        # 1. Try skill first
        matched_skill = match_skill_by_name(slash_name)
        if matched_skill:
            active_prompt = matched_skill.resolve_arguments(args, session_id=session_id)
        else:
            # 2. Check commands
            matched_command = get_command(slash_name)
            if matched_command:
                active_prompt = matched_command.resolve_arguments(args)
    else:
        # 3. Intent-based skill matching (natural language)
        matched_skill = match_skill_by_intent(data["text"])
        if matched_skill:
            active_prompt = matched_skill.prompt

    # Apply tool filter from matched skill
    if matched_skill and matched_skill.tools:
        filtered = get_tool_schemas(matched_skill.tools)
        if filtered:
            tool_schemas = filtered
            set_active_tool_filter(matched_skill.tools)

    # Notify UI
    if matched_skill:
        await send_event("skill_activated", {
            "name": matched_skill.name,
            "description": matched_skill.description,
            "type": "skill",
        })
    elif matched_command:
        await send_event("skill_activated", {
            "name": matched_command.name,
            "description": matched_command.description,
            "type": "command",
        })

    # Handle skill context=fork: run in isolated subagent
    if matched_skill and matched_skill.context == "fork":
        from tools.subagent import run_subagent
        await send_event("text_delta", {"text": ""})
        fork_system = active_prompt or matched_skill.prompt
        fork_prompt = data["text"]
        if msg_stripped.startswith("/"):
            parts = msg_stripped.split(None, 1)
            fork_prompt = parts[1] if len(parts) > 1 else matched_skill.description
        result, _fork_messages = await run_subagent(
            prompt=fork_prompt,
            system=fork_system,
            tool_names=matched_skill.tools or None,
        )
        await send_event("text_delta", {"text": result})
        await send_event("response_end", {})
        messages.append({"role": "assistant", "content": [{"type": "text", "text": result}]})
        conv_session_set(cid, "messages", messages)
        await save_conversation(cid)
        return

    # Resolve dynamic content in skill/command prompts
    if active_prompt:
        active_prompt = await resolve_dynamic_content(active_prompt)

    available_agents = await _list_user_agents(user_id)

    system = build_system_prompt(
        active_prompt,
        memory_context or None,
        user_system_prompt or None,
        project_config or None,
        rules_text or None,
        outputs_dir=str(user_outputs_dir),
        available_agents=available_agents,
    )

    # ── Agent callbacks ──
    async def on_text(text: str) -> None:
        await send_event("text_delta", {"text": text})

    async def on_tool_start(name: str, tool_input: dict[str, Any]) -> str:
        from tools.registry import get_tool_description
        tool_id = f"tool_{uuid.uuid4().hex[:8]}"
        await send_event("tool_start", {
            "tool_id": tool_id,
            "name": name,
            "input": tool_input,
            "description": get_tool_description(name),
        })
        return tool_id

    async def on_tool_end(tool_id: str, result: str) -> None:
        is_error = False
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and parsed.get("error"):
                is_error = True
        except (json.JSONDecodeError, TypeError):
            pass

        await send_event("tool_end", {
            "tool_id": tool_id,
            "result": result,
            "is_error": is_error,
        })
        await send_images_if_any(tool_id, result, send_event)

    # Wrap on_tool_start/end to track names for chart detection
    _tool_names: dict[str, str] = {}

    async def on_tool_start_wrapper(name: str, tool_input: dict[str, Any]) -> str:
        tool_id = await on_tool_start(name, tool_input)
        _tool_names[tool_id] = name
        _file_keys = ("file_path", "path", "pattern")
        for key in _file_keys:
            val = tool_input.get(key)
            if val and isinstance(val, str):
                af = conv_session_get(cid, "active_files") or []
                if val not in af:
                    af.append(val)
                    conv_session_set(cid, "active_files", af)
                break
        return tool_id

    async def on_tool_end_wrapper(tool_id: str, result: str) -> None:
        await on_tool_end(tool_id, result)
        tool_name = _tool_names.pop(tool_id, "")
        await send_chart_if_any(tool_id, tool_name, result, send_event)
        await send_tables_if_any(tool_id, tool_name, result, send_event)

    async def on_permission_check(name: str, tool_input: dict[str, Any]) -> bool | str:
        from core.permission_modes import PermissionMode, check_permission

        mode_str = connection_get(session_id, "permission_mode") or "default"
        mode = PermissionMode(mode_str)
        result = check_permission(mode, name, tool_input)

        if result is True:
            return True
        if isinstance(result, str):
            return result
        # result is None -- ask the user
        req_id = f"perm_{uuid.uuid4().hex[:8]}"
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        pending_permissions[req_id] = future

        await send_event("permission_request", {
            "request_id": req_id,
            "tool_name": name,
            "summary": summarize_tool_call(name, tool_input),
            "permission_mode": mode_str,
        })

        try:
            allowed = await asyncio.wait_for(future, timeout=120.0)
        except asyncio.TimeoutError:
            return "Timed out waiting for user permission"
        finally:
            pending_permissions.pop(req_id, None)

        return True if allowed else "User denied this action"

    # Model selection: per-session override > skill override > routing > config default
    active_model = conv_session_get(cid, "active_model") or None
    if not active_model and matched_skill and getattr(matched_skill, "model", None):
        active_model = matched_skill.model
    if not active_model:
        from core.router import select_model
        active_model = await select_model(data["text"], user_id=user_id, cid=cid)

    try:
        async with async_trace_span("agent.handle_message", user_id=user_id, cid=cid):
            messages = await agent.run(
                messages=messages,
                tools=tool_schemas,
                system=system,
                on_text=on_text,
                on_tool_start=on_tool_start_wrapper,
                on_tool_end=on_tool_end_wrapper,
                on_permission_check=on_permission_check,
                model=active_model,
            )
        conv_session_set(cid, "messages", messages)
        conv_id = await save_conversation(cid)
        usage_data = {}
        context_tokens = 0
        try:
            from core.usage import get_user_usage_summary
            from core.context import count_message_tokens
            _uid = connection_get(session_id, "user_id") or "default"
            usage_data = await get_user_usage_summary(_uid)
            context_tokens = await count_message_tokens(messages, system, tool_schemas)
        except Exception:
            pass
        await send_event("turn_complete", {
            "conversation_id": conv_id,
            "usage": usage_data,
            "context_tokens": context_tokens,
            "model": active_model,
        })
    except asyncio.CancelledError:
        logger.info("Agent generation cancelled by user")
        conv_session_set(cid, "messages", messages)
        await save_conversation(cid)
        await send_event("generation_cancelled", {})
    except Exception as e:
        logger.error(f"Agent error: {e}", exc_info=True)
        record_error("agent")
        conv_session_set(cid, "messages", messages)
        await save_conversation(cid)
        await send_event("error", {"message": str(e)})
