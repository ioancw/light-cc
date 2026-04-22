"""Built-in slash command handlers -- /plugin and /schedule."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.plugin_loader import get_plugin_loader

logger = logging.getLogger(__name__)

# Resolved at import time from server module
_PROJECT_ROOT: Path | None = None


def set_project_root(root: Path) -> None:
    global _PROJECT_ROOT
    _PROJECT_ROOT = root


def _get_project_root() -> Path:
    if _PROJECT_ROOT is not None:
        return _PROJECT_ROOT
    return Path(__file__).resolve().parent.parent


_ADMIN_SUBCOMMANDS = {"install", "update", "uninstall", "remove"}


async def handle_plugin_command(args: str, *, user_is_admin: bool = False) -> str:
    """Handle /plugin install|list|uninstall commands.

    install/update/uninstall are admin-only — they clone code and copy it into
    the project tree, which is effectively arbitrary code execution.
    """
    import shutil
    import subprocess as _sp

    parts = args.strip().split(None, 1)
    subcommand = parts[0].lower() if parts else ""
    sub_args = parts[1].strip() if len(parts) > 1 else ""

    if subcommand in _ADMIN_SUBCOMMANDS and not user_is_admin:
        return (
            f"Permission denied: `/plugin {subcommand}` is restricted to administrators. "
            "Contact an admin to install or update plugins."
        )

    loader = get_plugin_loader()
    plugins_dir = _get_project_root() / "plugins"
    plugins_dir.mkdir(exist_ok=True)

    if subcommand == "install":
        if not sub_args:
            return "Usage: `/plugin install <github-url-or-path>`"

        source = sub_args
        # Local path
        local_path = Path(source).expanduser()
        if local_path.exists() and local_path.is_dir():
            manifest = local_path / ".claude-plugin" / "plugin.json"
            if not manifest.exists():
                return f"No `.claude-plugin/plugin.json` found in `{source}`"
            info = await loader.load_plugin(local_path)
            if info:
                return (
                    f"**Plugin installed from local path**\n\n"
                    f"- **Name:** {info.name}\n"
                    f"- **Version:** {info.version}\n"
                    f"- **Description:** {info.description}\n"
                    f"- **Skills:** {', '.join(info.skills) or 'none'}\n"
                    f"- **Commands:** {', '.join(info.commands) or 'none'}\n"
                    f"- **MCP servers:** {', '.join(info.mcp_servers) or 'none'}"
                )
            return f"Failed to load plugin from `{source}`"

        # Git URL -- clone into plugins/
        url = source
        if "/" in source and not source.startswith(("http://", "https://", "git@")):
            url = f"https://github.com/{source}.git"
        elif source.startswith(("http://", "https://")) and not source.endswith(".git"):
            url = source + ".git"

        repo_name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        target_dir = plugins_dir / repo_name

        if target_dir.exists():
            try:
                proc = _sp.run(
                    ["git", "-C", str(target_dir), "pull", "--ff-only"],
                    capture_output=True, text=True, timeout=60,
                )
                if proc.returncode != 0:
                    return f"Failed to update `{repo_name}`:\n```\n{proc.stderr.strip()}\n```"
            except Exception as e:
                return f"Failed to update `{repo_name}`: {e}"
        else:
            try:
                proc = _sp.run(
                    ["git", "clone", "--depth", "1", url, str(target_dir)],
                    capture_output=True, text=True, timeout=120,
                )
                if proc.returncode != 0:
                    return f"Failed to clone `{url}`:\n```\n{proc.stderr.strip()}\n```"
            except Exception as e:
                return f"Failed to clone `{url}`: {e}"

        manifest = target_dir / ".claude-plugin" / "plugin.json"
        if not manifest.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
            return f"Cloned `{repo_name}` but no `.claude-plugin/plugin.json` found. Removed."

        # Refuse cloned trees that contain symlinks — same rule as plugin_manager.
        from core.plugin_manager import _reject_symlinks, PluginError
        try:
            _reject_symlinks(target_dir)
        except PluginError as e:
            shutil.rmtree(target_dir, ignore_errors=True)
            return f"Rejected `{repo_name}`: {e}"

        info = await loader.load_plugin(target_dir)
        if not info:
            return f"Cloned `{repo_name}` but failed to load plugin."

        return (
            f"**Plugin installed**\n\n"
            f"- **Name:** {info.name}\n"
            f"- **Version:** {info.version}\n"
            f"- **Description:** {info.description}\n"
            f"- **Skills:** {', '.join(info.skills) or 'none'}\n"
            f"- **Commands:** {', '.join(info.commands) or 'none'}\n"
            f"- **MCP servers:** {', '.join(info.mcp_servers) or 'none'}"
        )

    elif subcommand == "list":
        plugins = loader.list_plugins()
        if not plugins:
            return "No plugins installed."
        lines = ["**Installed plugins**\n"]
        for p in plugins:
            lines.append(
                f"- **{p.name}** v{p.version} -- {p.description or 'no description'}\n"
                f"  Skills: {', '.join(p.skills) or 'none'} | "
                f"Commands: {', '.join(p.commands) or 'none'} | "
                f"MCP: {', '.join(p.mcp_servers) or 'none'}"
            )
        return "\n".join(lines)

    elif subcommand in ("uninstall", "remove"):
        if not sub_args:
            return "Usage: `/plugin uninstall <name>`"

        name = sub_args
        info = loader.get_plugin(name)
        if not info:
            return f"Plugin `{name}` not found. Use `/plugin list` to see installed plugins."

        plugin_path = info.path
        await loader.unload_plugin(name)

        try:
            if plugin_path.is_relative_to(plugins_dir):
                shutil.rmtree(plugin_path, ignore_errors=True)
                return f"Plugin `{name}` uninstalled and removed from disk."
            else:
                return f"Plugin `{name}` unloaded. Directory `{plugin_path}` left in place (external path)."
        except Exception as e:
            return f"Plugin `{name}` unloaded but failed to remove directory: {e}"

    elif subcommand == "update":
        if not sub_args:
            return "Usage: `/plugin update <name>`"

        name = sub_args
        info = loader.get_plugin(name)
        if not info:
            return f"Plugin `{name}` not found."

        plugin_path = info.path
        if not (plugin_path / ".git").exists():
            return f"Plugin `{name}` is not a git repo, cannot update."

        try:
            proc = _sp.run(
                ["git", "-C", str(plugin_path), "pull", "--ff-only"],
                capture_output=True, text=True, timeout=60,
            )
            if proc.returncode != 0:
                return f"Failed to update `{name}`:\n```\n{proc.stderr.strip()}\n```"
        except Exception as e:
            return f"Failed to update `{name}`: {e}"

        await loader.unload_plugin(name)
        new_info = await loader.load_plugin(plugin_path)
        if not new_info:
            return f"Updated `{name}` but failed to reload."

        return (
            f"**Plugin updated**\n\n"
            f"- **Name:** {new_info.name}\n"
            f"- **Version:** {new_info.version}\n"
            f"- **Description:** {new_info.description}\n"
            f"- **Skills:** {', '.join(new_info.skills) or 'none'}\n"
            f"- **Commands:** {', '.join(new_info.commands) or 'none'}\n"
            f"- **MCP servers:** {', '.join(new_info.mcp_servers) or 'none'}"
        )

    else:
        return (
            "**Plugin commands**\n\n"
            "- `/plugin install <github-url-or-owner/repo>` -- install from GitHub\n"
            "- `/plugin install <local-path>` -- install from local directory\n"
            "- `/plugin list` -- show installed plugins\n"
            "- `/plugin update <name>` -- pull latest and reload\n"
            "- `/plugin uninstall <name>` -- remove a plugin"
        )


async def handle_schedule_command(args: str, user_id: str) -> str:
    """Handle /schedule create|list|enable|disable|delete|runs|run commands."""
    from croniter import croniter
    from core.schedule_crud import (
        create_schedule, list_schedules, resolve_schedule,
        update_schedule, delete_schedule, get_schedule_runs,
        trigger_schedule_now,
    )

    parts = args.strip().split(None, 1)
    subcommand = parts[0].lower() if parts else ""
    sub_args = parts[1].strip() if len(parts) > 1 else ""

    if subcommand == "create":
        import shlex
        try:
            tokens = shlex.split(sub_args)
        except ValueError:
            tokens = sub_args.split()

        # Extract --tz flag if present
        user_tz = "UTC"
        filtered_tokens = []
        i = 0
        while i < len(tokens):
            if tokens[i] == "--tz" and i + 1 < len(tokens):
                user_tz = tokens[i + 1]
                i += 2
            else:
                filtered_tokens.append(tokens[i])
                i += 1
        tokens = filtered_tokens

        if len(tokens) < 3:
            return (
                "Usage: `/schedule create <name> <cron> <prompt> [--tz <timezone>]`\n\n"
                "Examples:\n"
                "- `/schedule create \"Morning Brief\" \"0 9 * * *\" Summarize overnight market moves`\n"
                "- `/schedule create \"Hourly Check\" \"0 * * * *\" Check portfolio risk metrics --tz Europe/Bucharest`\n\n"
                "Without `--tz`, cron times are interpreted as UTC."
            )

        name = tokens[0]
        cron_expr = tokens[1]
        prompt = " ".join(tokens[2:])

        if not croniter.is_valid(cron_expr):
            return (
                f"Invalid cron expression: `{cron_expr}`\n\n"
                "Format: `minute hour day month weekday`\n"
                "Examples: `0 9 * * *` (daily 9am), `*/30 * * * *` (every 30 min), `0 9 * * 1` (Mondays 9am)"
            )

        # Validate timezone
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(user_tz)
        except (KeyError, Exception):
            return f"Invalid timezone: `{user_tz}`. Use IANA names like `Europe/Bucharest`, `US/Eastern`, `UTC`."

        try:
            sched = await create_schedule(user_id, name, cron_expr, prompt, user_tz)
        except Exception as e:
            return f"Failed to create schedule: {e}"

        next_run = sched.next_run_at.strftime("%Y-%m-%d %H:%M UTC") if sched.next_run_at else "unknown"
        return (
            f"**Schedule created**\n\n"
            f"- **ID:** `{sched.id[:8]}`\n"
            f"- **Name:** {sched.name}\n"
            f"- **Cron:** `{sched.cron_expression}`\n"
            f"- **Timezone:** {sched.user_timezone}\n"
            f"- **Prompt:** {sched.prompt}\n"
            f"- **Next run:** {next_run}"
        )

    elif subcommand == "list":
        schedules = await list_schedules(user_id)
        if not schedules:
            return "No schedules. Use `/schedule create` to add one."
        lines = ["**Scheduled tasks**\n"]
        for s in schedules:
            status = "enabled" if s.enabled else "disabled"
            tz_label = s.user_timezone or "UTC"
            next_run = s.next_run_at.strftime("%Y-%m-%d %H:%M UTC") if s.next_run_at else "n/a"
            last_run = s.last_run_at.strftime("%Y-%m-%d %H:%M UTC") if s.last_run_at else "never"
            lines.append(
                f"- `{s.id[:8]}` **{s.name}** ({status})\n"
                f"  Cron: `{s.cron_expression}` ({tz_label}) | Next: {next_run} | Last: {last_run}\n"
                f"  Prompt: {s.prompt[:100]}{'...' if len(s.prompt) > 100 else ''}"
            )
        return "\n".join(lines)

    elif subcommand == "enable":
        if not sub_args:
            return "Usage: `/schedule enable <name>`"
        sched = await resolve_schedule(sub_args, user_id)
        if not sched:
            return f"Schedule `{sub_args}` not found."
        await update_schedule(sched.id, user_id, enabled=True)
        return f"Schedule `{sched.name}` enabled."

    elif subcommand == "disable":
        if not sub_args:
            return "Usage: `/schedule disable <name>`"
        sched = await resolve_schedule(sub_args, user_id)
        if not sched:
            return f"Schedule `{sub_args}` not found."
        await update_schedule(sched.id, user_id, enabled=False)
        return f"Schedule `{sched.name}` disabled."

    elif subcommand == "delete":
        if not sub_args:
            return "Usage: `/schedule delete <name>`"
        sched = await resolve_schedule(sub_args, user_id)
        if not sched:
            return f"Schedule `{sub_args}` not found."
        await delete_schedule(sched.id, user_id)
        return f"Schedule `{sched.name}` deleted."

    elif subcommand == "runs":
        if not sub_args:
            return "Usage: `/schedule runs <name>`"
        sched = await resolve_schedule(sub_args, user_id)
        if not sched:
            return f"Schedule `{sub_args}` not found."
        runs = await get_schedule_runs(sched.id, user_id, limit=10)
        if not runs:
            return f"No runs yet for `{sched.name}`."
        lines = [f"**Recent runs for {sched.name}**\n"]
        for r in runs:
            started = r.started_at.strftime("%Y-%m-%d %H:%M UTC")
            duration = ""
            if r.finished_at:
                secs = (r.finished_at - r.started_at).total_seconds()
                duration = f" ({secs:.0f}s)"
            preview = ""
            if r.result:
                preview = f"\n  Result: {r.result[:120]}{'...' if len(r.result) > 120 else ''}"
            elif r.error:
                preview = f"\n  Error: {r.error[:120]}{'...' if len(r.error) > 120 else ''}"
            lines.append(f"- **{r.status}** at {started}{duration}{preview}")
        return "\n".join(lines)

    elif subcommand == "run":
        if not sub_args:
            return "Usage: `/schedule run <name>`"
        sched = await resolve_schedule(sub_args, user_id)
        if not sched:
            return f"Schedule `{sub_args}` not found."
        await trigger_schedule_now(sched.id, user_id)
        return f"Triggered `{sched.name}`. You'll get a notification when it completes."

    else:
        return (
            "**Schedule commands**\n\n"
            "- `/schedule create <name> <cron> <prompt> [--tz <timezone>]` -- create a scheduled task\n"
            "- `/schedule list` -- show all schedules (with IDs)\n"
            "- `/schedule enable <name|id>` -- enable a schedule\n"
            "- `/schedule disable <name|id>` -- disable a schedule\n"
            "- `/schedule delete <name|id>` -- delete a schedule\n"
            "- `/schedule runs <name|id>` -- show recent run history\n"
            "- `/schedule run <name|id>` -- trigger immediately\n\n"
            "**Cron format:** `minute hour day month weekday`\n\n"
            "| Pattern | Meaning |\n"
            "|---|---|\n"
            "| `0 9 * * *` | Daily at 9:00 AM |\n"
            "| `*/30 * * * *` | Every 30 minutes |\n"
            "| `0 9 * * 1-5` | Weekdays at 9:00 AM |\n"
            "| `0 9 * * 1` | Mondays at 9:00 AM |\n"
            "| `0 */6 * * *` | Every 6 hours |\n"
            "| `0 0 1 * *` | First of each month |\n\n"
            "**Examples**\n\n"
            "```\n"
            "/schedule create \"Morning Brief\" \"0 9 * * 1-5\" Summarize overnight market moves, key economic data releases, and any notable earnings\n"
            "/schedule create \"Risk Report\" \"0 */6 * * *\" Run VaR and CVaR on current portfolio positions and flag any breaches\n"
            "/schedule create \"Paper Digest\" \"0 8 * * 1\" Search for new research papers on stochastic volatility and summarize findings\n"
            "/schedule create \"Data Refresh\" \"0 7 * * 1-5\" /analyze portfolio.csv\n"
            "/schedule create \"Weekly Charts\" \"0 9 * * 1\" /chart weekly performance across all tracked indices\n"
            "```\n\n"
            "Prompts starting with `/` will use the matching skill or command. "
            "Plain text prompts run a general-purpose agent with full tool access."
        )


def _agent_source_label(source: str) -> str:
    """Human-readable source label. ``plugin:foo`` collapses to ``foo``."""
    if source.startswith("plugin:"):
        return f"plugin ({source.split(':', 1)[1]})"
    if source == "yaml":
        return "yaml (agents/<name>/AGENT.md)"
    if source == "user":
        return "user"
    return source or "unknown"


async def list_agents_for_client(user_id: str) -> list[dict[str, str]]:
    """Build the agents-roster payload sent over WS for the `@agent-` picker.

    Filters to enabled agents only -- a disabled agent in the picker would
    just produce a dispatch error, which is worse than not listing it. The
    `name` field is the literal token the user types after `@agent-`, so
    plugin agents (whose `AgentDefinition.name` already carries the
    `<plugin>:<name>` colon namespace) flow through unchanged.
    """
    from core.agent_crud import list_agents

    try:
        agents = await list_agents(user_id)
    except Exception:
        # Picker is best-effort; never break the WS handshake on a DB hiccup.
        return []
    return [
        {
            "name": a.name,
            "description": a.description or "",
            "source": a.source or "user",
        }
        for a in agents
        if a.enabled
    ]


def _resolve_agent_arg(name_or_id: str, agents: list) -> "AgentDefinition | None":  # noqa: F821
    """Resolve an agent by name OR by id-prefix (matches /schedule's pattern)."""
    needle = name_or_id.strip()
    for a in agents:
        if a.name == needle or a.id == needle or a.id.startswith(needle):
            return a
    return None


async def handle_agents_command(args: str, user_id: str) -> str:
    """Handle ``/agents`` (bare list + ``enable`` / ``disable`` / ``show``).

    Parity with CC's built-in ``/agents``: prints the roster grouped by
    source so the user can tell user-authored, project, and plugin-shipped
    agents apart at a glance, with the ``@agent-<name>`` dispatch token
    next to each row. The ``create`` subcommand is intentionally deferred
    to W1 (interactive wizard) -- bare ``/agents create`` returns a
    pointer there.
    """
    from core.agent_crud import get_agent_by_name, list_agents, update_agent

    parts = args.strip().split(None, 1)
    subcommand = parts[0].lower() if parts else ""
    sub_args = parts[1].strip() if len(parts) > 1 else ""

    if subcommand in ("", "list"):
        agents = await list_agents(user_id)
        if not agents:
            return (
                "No agents yet.\n\n"
                "Create one with `/agents create <name>` (coming soon -- "
                "for now, drop an `AGENT.md` into `agents/<name>/`)."
            )

        # Group by source so user-edited, project-yaml, and plugin agents
        # are visually separated. Within a group, sort by name.
        from collections import defaultdict
        groups: dict[str, list] = defaultdict(list)
        for a in agents:
            groups[a.source or "user"].append(a)
        for src in groups:
            groups[src].sort(key=lambda a: a.name)

        # Stable group ordering: user first, then yaml, then plugins alphabetically.
        def _group_sort_key(src: str) -> tuple[int, str]:
            if src == "user":
                return (0, "")
            if src == "yaml":
                return (1, "")
            return (2, src)

        lines = ["**Agents**\n"]
        for src in sorted(groups.keys(), key=_group_sort_key):
            lines.append(f"\n_{_agent_source_label(src)}_")
            for a in groups[src]:
                state = "" if a.enabled else " _(disabled)_"
                model = f" · model: `{a.model}`" if a.model else ""
                lines.append(
                    f"- **{a.name}**{state} -- {a.description}{model}\n"
                    f"  Dispatch: `@agent-{a.name} <prompt>`"
                )
        lines.append("\n_Subcommands: `/agents show <name>`, `/agents enable <name>`, `/agents disable <name>`._")
        return "\n".join(lines)

    if subcommand == "show":
        if not sub_args:
            return "Usage: `/agents show <name>`"
        agent = await get_agent_by_name(sub_args, user_id)
        if not agent:
            agents = await list_agents(user_id)
            agent = _resolve_agent_arg(sub_args, agents)
        if not agent:
            return f"Agent `{sub_args}` not found. Try `/agents` to see available names."
        tools_label = ", ".join(agent.tools_list or []) or "(inherits all)"
        skills_label = ", ".join(agent.skills_list or []) or "(inherits all)"
        model_label = agent.model or "(inherits session default)"
        state_label = "enabled" if agent.enabled else "disabled"
        return (
            f"**Agent: {agent.name}**\n\n"
            f"- **Source:** {_agent_source_label(agent.source or 'user')}\n"
            f"- **State:** {state_label}\n"
            f"- **Description:** {agent.description}\n"
            f"- **Model:** {model_label}\n"
            f"- **Tools:** {tools_label}\n"
            f"- **Skills:** {skills_label}\n"
            f"- **Memory scope:** {agent.memory_scope}\n"
            f"- **Max turns / timeout:** {agent.max_turns} / {agent.timeout_seconds}s\n"
            f"- **Dispatch:** `@agent-{agent.name} <prompt>`\n\n"
            f"**System prompt**\n\n```\n{agent.system_prompt}\n```"
        )

    if subcommand in ("enable", "disable"):
        if not sub_args:
            return f"Usage: `/agents {subcommand} <name>`"
        agent = await get_agent_by_name(sub_args, user_id)
        if not agent:
            agents = await list_agents(user_id)
            agent = _resolve_agent_arg(sub_args, agents)
        if not agent:
            return f"Agent `{sub_args}` not found."
        target = subcommand == "enable"
        if agent.enabled == target:
            return f"Agent `{agent.name}` is already {subcommand}d."
        await update_agent(agent.id, user_id, enabled=target)
        verb = "enabled" if target else "disabled"
        hint = f" `@agent-{agent.name}` will now dispatch." if target else (
            f" `@agent-{agent.name}` will return a disabled-agent error until re-enabled."
        )
        return f"Agent `{agent.name}` {verb}.{hint}"

    if subcommand == "create":
        # The interactive wizard runs in chat -- it needs a live session
        # to track multi-turn state, so it's wired in handlers/agent_handler.py
        # directly. Direct callers (tests, scripts) get the manual path.
        return (
            "`/agents create [<name>]` runs as an interactive wizard in chat. "
            "Type it into the chat box to start.\n\n"
            "Prefer to write the file by hand? Drop `agents/<name>/AGENT.md` "
            "into your project and run `/reload`."
        )

    return (
        "**Agent commands**\n\n"
        "- `/agents` -- list agents grouped by source (user / yaml / plugin)\n"
        "- `/agents show <name>` -- dump the agent's full definition + system prompt\n"
        "- `/agents enable <name>` -- re-enable a disabled agent\n"
        "- `/agents disable <name>` -- disable so `@agent-<name>` short-circuits with an error\n"
        "- `/agents create <name>` -- (coming soon) interactive wizard\n\n"
        "Dispatch any agent in chat with `@agent-<name> <prompt>`."
    )


def _skill_source_label(skill) -> str:  # noqa: ANN001
    """Human-readable source label for a SkillDef.

    Plugin-namespaced skills (``plugin:name``) collapse to ``plugin (name)``.
    Otherwise we fall back to the parent directory of ``skill_dir`` so
    project-vs-personal-vs-plugin shows up in the listing.
    """
    if ":" in skill.name:
        plugin = skill.name.split(":", 1)[0]
        return f"plugin ({plugin})"
    if skill.kind == "legacy-command":
        return "legacy command"
    sd = (skill.skill_dir or "").replace("\\", "/")
    if "/plugins/" in sd:
        return "plugin"
    if "/.claude/skills" in sd:
        return ".claude/skills"
    if sd.endswith("/skills") or "/skills/" in sd:
        return "skills/"
    return "user"


async def handle_skills_command(args: str, user_id: str) -> str:
    """Handle ``/skills`` (bare list + ``show`` / ``enable`` / ``disable``).

    Mirrors ``/agents``: roster grouped by source, ``show`` dumps the full
    SKILL.md definition, ``enable`` / ``disable`` flip the on-disk
    ``user-invocable`` + ``disable-model-invocation`` flags so reload picks
    them up. ``create`` is intercepted in ``handlers/agent_handler.py`` (it
    needs the live session_id to drive the wizard).
    """
    from pathlib import Path
    from skills.loader import set_skill_enabled
    from skills.registry import get_skill, list_skills, reload_skills

    parts = args.strip().split(None, 1)
    subcommand = parts[0].lower() if parts else ""
    sub_args = parts[1].strip() if len(parts) > 1 else ""

    if subcommand in ("", "list"):
        skills = list_skills()
        if not skills:
            return (
                "No skills loaded.\n\n"
                "Create one with `/skills create <name>` (interactive wizard "
                "in chat) or drop a `SKILL.md` into `skills/<name>/`."
            )

        from collections import defaultdict
        groups: dict[str, list] = defaultdict(list)
        for s in skills:
            groups[_skill_source_label(s)].append(s)
        for src in groups:
            groups[src].sort(key=lambda s: s.name)

        # Stable ordering: user-authored first, then plugin/legacy, by alpha.
        def _group_sort_key(src: str) -> tuple[int, str]:
            if src == "user":
                return (0, "")
            if src == "skills/":
                return (1, "")
            if src == ".claude/skills":
                return (2, "")
            if src == "legacy command":
                return (3, "")
            return (4, src)

        lines = ["**Skills**\n"]
        for src in sorted(groups.keys(), key=_group_sort_key):
            lines.append(f"\n_{src}_")
            for s in groups[src]:
                hidden = "" if s.user_invocable else " _(disabled)_"
                hint = f" `{s.argument_hint}`" if s.argument_hint else ""
                lines.append(
                    f"- **/{s.name}**{hint}{hidden} -- {s.description or '(no description)'}"
                )
        lines.append("\n_Subcommands: `/skills show <name>`, `/skills enable <name>`, `/skills disable <name>`, `/skills create <name>`._")
        return "\n".join(lines)

    if subcommand == "show":
        if not sub_args:
            return "Usage: `/skills show <name>`"
        skill = get_skill(sub_args)
        if not skill:
            return f"Skill `{sub_args}` not found. Try `/skills` to see available names."
        tools_label = ", ".join(skill.tools) if skill.tools else "(inherits all)"
        state_label = "enabled" if skill.user_invocable else "disabled"
        model_label = skill.model or "(inherits session default)"
        argh = f"`{skill.argument_hint}`" if skill.argument_hint else "_(none)_"
        return (
            f"**Skill: /{skill.name}**\n\n"
            f"- **Source:** {_skill_source_label(skill)}\n"
            f"- **State:** {state_label}\n"
            f"- **Description:** {skill.description or '(none)'}\n"
            f"- **Argument hint:** {argh}\n"
            f"- **Tools:** {tools_label}\n"
            f"- **Model:** {model_label}\n"
            f"- **Auto-invoke:** {'no' if skill.disable_model_invocation else 'yes'}\n"
            f"- **Skill dir:** `{skill.skill_dir or '(unknown)'}`\n\n"
            f"**Body**\n\n```\n{skill.prompt}\n```"
        )

    if subcommand in ("enable", "disable"):
        if not sub_args:
            return f"Usage: `/skills {subcommand} <name>`"
        skill = get_skill(sub_args)
        if not skill:
            return f"Skill `{sub_args}` not found."
        if not skill.skill_dir:
            return f"Skill `{sub_args}` has no on-disk source -- can't toggle."

        target = subcommand == "enable"
        if skill.user_invocable == target and not skill.disable_model_invocation == (not target):
            return f"Skill `/{skill.name}` is already {subcommand}d."

        # Locate the SKILL.md (or first *.md) under skill_dir.
        sdir = Path(skill.skill_dir)
        candidates = [sdir / "SKILL.md"]
        candidates += sorted(sdir.glob("*.md"))
        skill_path = next((p for p in candidates if p.exists()), None)
        if not skill_path:
            return f"Couldn't find a markdown file under `{sdir}` to edit."

        try:
            set_skill_enabled(skill_path, target)
        except Exception as e:
            return f"Failed to {subcommand} `/{skill.name}`: {e}"

        reload_skills()
        verb = "enabled" if target else "disabled"
        hint = (
            f" `/{skill.name}` will now appear in autocomplete."
            if target else
            f" `/{skill.name}` will no longer appear in `/` autocomplete or auto-match."
        )
        return f"Skill `/{skill.name}` {verb}.{hint}"

    if subcommand == "create":
        # The wizard runs in chat (needs session_id). Direct callers
        # (tests, scripts) get a chat-pointer + manual path.
        return (
            "`/skills create [<name>]` runs as an interactive wizard in chat. "
            "Type it into the chat box to start.\n\n"
            "Prefer to write the file by hand? Drop `skills/<name>/SKILL.md` "
            "into your project and run `/reload`."
        )

    return (
        "**Skill commands**\n\n"
        "- `/skills` -- list skills grouped by source\n"
        "- `/skills show <name>` -- dump the skill's full definition + body\n"
        "- `/skills enable <name>` -- re-enable a disabled skill\n"
        "- `/skills disable <name>` -- hide from `/` autocomplete and disable auto-invoke\n"
        "- `/skills create <name>` -- interactive wizard (run from chat)\n"
    )
