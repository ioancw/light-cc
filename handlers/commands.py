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


async def handle_plugin_command(args: str) -> str:
    """Handle /plugin install|list|uninstall commands."""
    import shutil
    import subprocess as _sp

    parts = args.strip().split(None, 1)
    subcommand = parts[0].lower() if parts else ""
    sub_args = parts[1].strip() if len(parts) > 1 else ""
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

        if len(tokens) < 3:
            return (
                "Usage: `/schedule create <name> <cron> <prompt>`\n\n"
                "Examples:\n"
                "- `/schedule create \"Morning Brief\" \"0 9 * * *\" Summarize overnight market moves`\n"
                "- `/schedule create \"Hourly Check\" \"0 * * * *\" Check portfolio risk metrics`"
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

        try:
            sched = await create_schedule(user_id, name, cron_expr, prompt)
        except Exception as e:
            return f"Failed to create schedule: {e}"

        next_run = sched.next_run_at.strftime("%Y-%m-%d %H:%M UTC") if sched.next_run_at else "unknown"
        return (
            f"**Schedule created**\n\n"
            f"- **ID:** `{sched.id[:8]}`\n"
            f"- **Name:** {sched.name}\n"
            f"- **Cron:** `{sched.cron_expression}`\n"
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
            next_run = s.next_run_at.strftime("%Y-%m-%d %H:%M UTC") if s.next_run_at else "n/a"
            last_run = s.last_run_at.strftime("%Y-%m-%d %H:%M UTC") if s.last_run_at else "never"
            lines.append(
                f"- `{s.id[:8]}` **{s.name}** ({status})\n"
                f"  Cron: `{s.cron_expression}` | Next: {next_run} | Last: {last_run}\n"
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
            "- `/schedule create <name> <cron> <prompt>` -- create a scheduled task\n"
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
