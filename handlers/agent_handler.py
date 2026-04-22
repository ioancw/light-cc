"""Agent message handler -- processes user messages through the agentic loop.

Contains _handle_user_message and its supporting functions (title generation,
context summarization, agent callbacks).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

# ``@agent-<name> [prompt]`` -- chat surface for direct agent dispatch.
# Name is kebab-case; an optional ``<plugin>:<name>`` colon namespace is
# preserved through to ``get_agent_by_name`` (plugin agents store the
# colon literally in ``AgentDefinition.name``).
AGENT_MENTION_RE = re.compile(
    r"^@agent-([a-z0-9][a-z0-9-]*(?::[a-z0-9][a-z0-9-]*)?)(?:\s+(.*))?$",
    re.DOTALL,
)

from core import agent, agent_runs
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
from handlers.agents_wizard import (
    handle_wizard_input as agents_wizard_input,
    is_wizard_active as agents_wizard_active,
    start_wizard as start_agents_wizard,
)
from handlers.skills_wizard import (
    handle_wizard_input as skills_wizard_input,
    is_wizard_active as skills_wizard_active,
    start_wizard as start_skills_wizard,
)
from handlers.schedule_wizard import (
    handle_wizard_input as schedule_wizard_input,
    is_wizard_active as schedule_wizard_active,
    start_wizard as start_schedule_wizard,
)
from handlers.commands import (
    handle_agents_command,
    handle_plugin_command,
    handle_schedule_command,
    handle_skills_command,
    list_agents_for_client,
)
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

        async with get_db() as db:
            res = await db.execute(
                select(AgentDefinition.name, AgentDefinition.description).where(
                    AgentDefinition.user_id == user_id,
                    AgentDefinition.enabled.is_(True),
                )
            )
            return [(n, d) for n, d in res.all()]
    except Exception as e:
        logger.debug(f"_list_user_agents failed for {user_id}: {e}")
        return []


async def handle_user_message(
    session_id: str,
    cid: str,
    data: dict[str, Any],
    send_event: SendEvent,
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

    # ── Wizard intercept (agents + skills) ─────────────────────────────
    # When a wizard is mid-conversation for this session, every reply
    # belongs to it -- bypass slash routing, skill matching, the model,
    # and even conversation persistence so the back-and-forth doesn't
    # bloat chat history. The wizards own their own multi-turn state.
    if agents_wizard_active(session_id):
        project_dir = Path(settings.project_dir) if settings.project_dir else Path.cwd()
        outcome = await agents_wizard_input(
            session_id, user_id, data["text"], project_dir,
        )
        await send_event("text_delta", {"text": outcome.text})
        if outcome.agents_updated:
            await send_event("agents_updated", {"agents": await list_agents_for_client(user_id)})
        await send_event("turn_complete", {})
        return

    if skills_wizard_active(session_id):
        project_dir = Path(settings.project_dir) if settings.project_dir else Path.cwd()
        outcome = await skills_wizard_input(
            session_id, user_id, data["text"], project_dir,
        )
        await send_event("text_delta", {"text": outcome.text})
        if outcome.skills_updated:
            # Push the refreshed roster the same way /reload does.
            refreshed = [
                {"name": s.name, "description": s.description, "argument_hint": s.argument_hint}
                for s in list_skills() if s.user_invocable
            ]
            await send_event("skills_updated", {"skills": refreshed})
        await send_event("turn_complete", {})
        return

    if schedule_wizard_active(session_id):
        outcome = await schedule_wizard_input(session_id, user_id, data["text"])
        await send_event("text_delta", {"text": outcome.text})
        if outcome.schedules_updated:
            await send_event("schedules_updated", {})
        await send_event("turn_complete", {})
        return

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

    # ── @agent-<name> dispatch ─────────────────────────────────────────
    # Same surface CC exposes: typing ``@agent-foo Do X`` in chat invokes
    # the foo agent with prompt ``Do X``. Symmetric with the ``Agent`` tool
    # the model uses internally -- both end up at ``run_agent_once``.
    # No cross-category fallback: an unknown @agent- name falls through to
    # main Claude as literal text rather than escalating to skill/command.
    mention = AGENT_MENTION_RE.match(msg_stripped)
    if mention:
        agent_name = mention.group(1)
        agent_prompt = (mention.group(2) or "").strip()
        from core.agent_crud import get_agent_by_name
        agent_def = await get_agent_by_name(agent_name, user_id)
        if agent_def and agent_def.enabled:
            from core.agent_runner import run_agent_once
            await send_event("skill_activated", {
                "name": agent_def.name,
                "description": agent_def.description,
                "type": "agent",
            })
            try:
                result = await run_agent_once(
                    agent_def,
                    agent_prompt or f"Execute your task ({agent_def.name}).",
                    trigger_type="mention",
                    parent_session_id=session_id,
                    persist_conversation=False,
                )
                if result.status == "failed":
                    assistant_text = (
                        f"Agent `{agent_def.name}` failed: "
                        f"{result.error or 'unknown error'}"
                    )
                else:
                    assistant_text = result.result_text or (
                        f"(Agent `{agent_def.name}` returned no output.)"
                    )
            except Exception as e:
                logger.error(f"Agent mention dispatch failed: {e}", exc_info=True)
                assistant_text = f"Agent `{agent_def.name}` errored: {e}"

            await send_event("text_delta", {"text": assistant_text})
            messages.append({"role": "assistant", "content": [{"type": "text", "text": assistant_text}]})
            conv_session_set(cid, "messages", messages)
            conv_id = await save_conversation(cid)
            await send_event("turn_complete", {"conversation_id": conv_id})
            return

    if msg_stripped.startswith("/"):
        slash_name = msg_stripped.split()[0][1:]
        parts = msg_stripped.split(None, 1)
        args = parts[1] if len(parts) > 1 else ""

        # Built-in: /reload
        if slash_name == "reload":
            from skills.registry import reload_skills
            from commands.registry import reload_commands
            reload_skills()
            n_cmds = reload_commands()  # legacy-command subset
            n_skills = sum(1 for s in list_skills() if s.kind != "legacy-command")
            # Clear cached project config/rules so they're re-read too
            connection_set(session_id, "project_config", None)
            connection_set(session_id, "project_rules", None)
            await send_event("text_delta", {"text": f"Reloaded {n_skills} skills and {n_cmds} commands."})
            # Notify frontend of updated skill list (legacy commands already
            # included in ``list_skills()`` via the unified registry).
            refreshed = [
                {"name": s.name, "description": s.description, "argument_hint": s.argument_hint}
                for s in list_skills() if s.user_invocable
            ] + [
                {"name": "agents", "description": "List, enable, disable, or show your agents", "argument_hint": "list|show|enable|disable <name>"},
                {"name": "skills", "description": "List, enable, disable, show, or create skills", "argument_hint": "list|show|enable|disable|create <name>"},
                {"name": "context", "description": "Show context window usage breakdown", "argument_hint": ""},
                {"name": "plugin", "description": "Install, list, update, or uninstall plugins", "argument_hint": "install|list|update|uninstall <name-or-url>"},
                {"name": "schedule", "description": "Create, list, enable, disable, or delete scheduled agent tasks", "argument_hint": "create|list|enable|disable|delete|runs|run"},
                {"name": "reload", "description": "Reload all skills, commands, and project config from disk", "argument_hint": ""},
            ]
            await send_event("skills_updated", {"skills": refreshed})
            await send_event("agents_updated", {"agents": await list_agents_for_client(user_id)})
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
            # Re-check admin against DB rather than trusting connection state.
            user_is_admin = False
            if user_id and user_id != "default":
                from core.auth import get_user_by_id
                from core.database import get_db
                async with get_db() as db:
                    user = await get_user_by_id(db, user_id)
                    user_is_admin = bool(user and user.is_admin)
            result = await handle_plugin_command(args, user_is_admin=user_is_admin)
            await send_event("text_delta", {"text": result})
            sub = args.strip().split()[0].lower() if args.strip() else ""
            if sub in ("install", "uninstall", "remove", "update"):
                refreshed = [
                    {"name": s.name, "description": s.description, "argument_hint": s.argument_hint}
                    for s in list_skills() if s.user_invocable
                ] + [
                    {"name": "agents", "description": "List, enable, disable, or show your agents", "argument_hint": "list|show|enable|disable <name>"},
                    {"name": "context", "description": "Show context window usage breakdown", "argument_hint": ""},
                    {"name": "plugin", "description": "Install, list, update, or uninstall plugins", "argument_hint": "install|list|update|uninstall <name-or-url>"},
                ]
                await send_event("skills_updated", {"skills": refreshed})
                await send_event("agents_updated", {"agents": await list_agents_for_client(user_id)})
            await send_event("turn_complete", {})
            return

        # Built-in: /schedule
        # Bare ``/schedule`` opens the conversational wizard; explicit
        # subcommands (``create``/``list``/``run``/...) still route through
        # ``handle_schedule_command`` so power users keep the one-liner.
        if slash_name == "schedule":
            if not args.strip():
                first_prompt = start_schedule_wizard(session_id, user_id)
                await send_event("text_delta", {"text": first_prompt})
                await send_event("turn_complete", {})
                return
            result = await handle_schedule_command(args, user_id)
            await send_event("text_delta", {"text": result})
            await send_event("turn_complete", {})
            return

        # Built-in: /agents -- list, enable, disable, show, create.
        # ``create`` is intercepted here (not in handle_agents_command)
        # because the wizard needs the live session_id to persist its
        # state across turns. Mutating subcommands (enable/disable) bump
        # ``agents_updated`` so the frontend roster + ``@agent-`` picker
        # pick up the new state.
        if slash_name == "agents":
            sub = args.strip().split(None, 1)
            sub_cmd = sub[0].lower() if sub else ""
            if sub_cmd == "create":
                name_hint = sub[1].strip() if len(sub) > 1 else ""
                first_prompt = start_agents_wizard(session_id, user_id, name_hint)
                await send_event("text_delta", {"text": first_prompt})
                await send_event("turn_complete", {})
                return

            result = await handle_agents_command(args, user_id)
            await send_event("text_delta", {"text": result})
            if sub_cmd in ("enable", "disable"):
                await send_event("agents_updated", {"agents": await list_agents_for_client(user_id)})
            await send_event("turn_complete", {})
            return

        # Built-in: /skills -- list, show, enable, disable, create.
        # ``create`` is intercepted here (needs session_id for the wizard).
        # enable/disable mutate on-disk frontmatter and re-run reload, so
        # we push a refreshed ``skills_updated`` payload to keep the UI
        # picker in sync.
        if slash_name == "skills":
            sub = args.strip().split(None, 1)
            sub_cmd = sub[0].lower() if sub else ""
            if sub_cmd == "create":
                name_hint = sub[1].strip() if len(sub) > 1 else ""
                first_prompt = start_skills_wizard(session_id, user_id, name_hint)
                await send_event("text_delta", {"text": first_prompt})
                await send_event("turn_complete", {})
                return

            result = await handle_skills_command(args, user_id)
            await send_event("text_delta", {"text": result})
            if sub_cmd in ("enable", "disable"):
                refreshed = [
                    {"name": s.name, "description": s.description, "argument_hint": s.argument_hint}
                    for s in list_skills() if s.user_invocable
                ]
                await send_event("skills_updated", {"skills": refreshed})
            await send_event("turn_complete", {})
            return

        # Unified resolver: real SKILL.md skills and legacy commands/*.md
        # files both live in the skills registry now (commands are wrapped as
        # ``SkillDef(kind="legacy-command")``). The `kind` field only affects
        # UI labelling -- dispatch is identical.
        matched_skill = match_skill_by_name(slash_name)
        if matched_skill:
            active_prompt = matched_skill.resolve_arguments(args, session_id=session_id)
    else:
        # 3. Intent-based skill matching (natural language)
        matched_skill = match_skill_by_intent(data["text"])
        if matched_skill:
            active_prompt = matched_skill.prompt

    # ── Agent intent router ────────────────────────────────────────────
    # Belt-and-braces nudge: when no skill matched and the message isn't
    # an explicit slash, score the user's enabled agents against the
    # message. A strong match becomes a per-turn hint at the top of the
    # system prompt -- the model still chooses whether to delegate. We
    # never auto-dispatch from the matcher because the user retains the
    # right to free-form chat with main Claude even on adjacent intents.
    routing_hint: str | None = None
    if matched_skill is None and not msg_stripped.startswith("/") and not mention:
        try:
            from core.agent_crud import match_agent_by_intent
            matched_agent = await match_agent_by_intent(data["text"], user_id)
        except Exception as e:
            logger.debug(f"match_agent_by_intent failed: {e}")
            matched_agent = None
        if matched_agent:
            routing_hint = (
                f"This user message strongly matches the configured agent "
                f"`{matched_agent.name}` ({matched_agent.description}). "
                f"You MUST delegate by calling "
                f"`Agent(agent_type=\"{matched_agent.name}\", "
                f"prompt=\"<full original user message and any context>\")` "
                f"rather than handling the task inline. Do not paraphrase "
                f"or summarize the task before handing off. Only handle "
                f"inline if you are CERTAIN this agent does not fit the "
                f"request."
            )

    # Apply tool filter from matched skill
    if matched_skill and matched_skill.tools:
        filtered = get_tool_schemas(matched_skill.tools)
        if filtered:
            tool_schemas = filtered
            set_active_tool_filter(matched_skill.tools)

    # Notify UI -- the legacy-command vs skill distinction is preserved on
    # the wire so the frontend can label invocations correctly.
    if matched_skill:
        await send_event("skill_activated", {
            "name": matched_skill.name,
            "description": matched_skill.description,
            "type": "command" if matched_skill.kind == "legacy-command" else "skill",
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
        routing_hint=routing_hint,
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
                if val in af:
                    # Move to the end so oldest entries fall off first.
                    af.remove(val)
                af.append(val)
                # Cap to the most recently touched 50 — rules matching only
                # needs a recent window, and unbounded growth balloons the
                # session state that gets flushed to Redis.
                if len(af) > 50:
                    af = af[-50:]
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
        agent_runs.add_pending_permission(cid, req_id, future)

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
            agent_runs.pop_pending_permission(cid, req_id)

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
        messages = agent.repair_tool_use_pairs(messages)
        conv_session_set(cid, "messages", messages)
        await save_conversation(cid)
        await send_event("generation_cancelled", {})
    except Exception as e:
        logger.error(f"Agent error: {e}", exc_info=True)
        record_error("agent")
        messages = agent.repair_tool_use_pairs(messages)
        conv_session_set(cid, "messages", messages)
        await save_conversation(cid)
        await send_event("error", {"message": str(e)})
