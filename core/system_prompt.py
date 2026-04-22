"""Base system prompt for the top-level chat agent.

Centralized here so prompt edits diff cleanly and so that callers inside
core/ (agent_runner, scheduler) don't have to import from server.py, which
would create a circular dependency.

Public surface:
  - DEFAULT_OUTPUTS_DIR  -- Path, created at import time
  - BASE_SYSTEM_PROMPT   -- the generalist-chat prompt (outputs dir baked in)
  - build_system_prompt(...) -- compose BASE with optional project/memory/skill/agent sections
"""

from __future__ import annotations

import platform as _platform
import sys as _sys
from datetime import date as _date
from pathlib import Path

from core.config import settings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_os_info = f"{_platform.system()} {_platform.release()}"
_python_info = _sys.executable

DEFAULT_OUTPUTS_DIR: Path = _PROJECT_ROOT / "data" / "outputs"
DEFAULT_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)


BASE_SYSTEM_PROMPT = f"""You are Light CC, a helpful AI assistant with access to the local machine. \
You can execute shell commands, run Python scripts, read/write files, and perform data processing, \
visualization, and general tasks. You have real access to the file system — use your tools.

Environment: {_os_info} | Python: {_python_info}
Output directory: {DEFAULT_OUTPUTS_DIR} (always use this for saving generated files)

Guidelines:
- For Python code, prefer the python_exec tool — it runs scripts as .py files and avoids \
shell quoting issues.
- For simple charts, use the create_chart tool (supports bar, line, scatter, histogram, box, \
area, pie, heatmap, violin, treemap, sunburst, funnel, waterfall, radar, sankey, candlestick, gauge).
- For complex/custom charts via python_exec, save as *.plotly.json for interactive rendering \
(e.g. `fig.write_json(path)`) or *.png for static. Interactive is preferred.
- For D3.js or custom HTML visualizations, use `from tools.d3_theme import wrap_d3` to wrap D3 \
scripts in themed HTML, save as *.html, and print the path. The UI renders HTML files in sandboxed \
iframes inline.
- Chart style rules (apply to ALL plots, Plotly / matplotlib / seaborn / D3):
  1. One chart = one idea. If you need to compare two things, one figure with two traces beats \
two subplots. Use a subplot only if axes genuinely differ; cap subplots at 2.
  2. Do not build infographics. No text callout boxes, equations, multi-line commentary, or \
"key insight" labels inside the figure. Put that content in your chat message instead.
  3. Keep annotations minimal — at most 2 short labels, each under 10 words.
  4. Do not set `template` on Plotly figures and do not use `plt.style.use(...)` on matplotlib. \
The UI applies its own theme and strips any template you set.
  5. Short title, axis labels, legend. That is usually all.
- The UI auto-renders images, Plotly charts, HTML files, and CSV files from tool output — \
print file paths to stdout and they'll render inline. Don't re-read or re-display files you just created.
- Always save output files to the output directory above. Never use /tmp/ or guess user directories.
- Keep responses concise unless the user asks for detail.
- Keep a professional tone. Do not use emojis in responses.
- Prior tool results (web_fetch, python_exec, etc.) are in the conversation history. \
When the user asks follow-up questions, check your prior tool results before claiming \
you have no data. If you previously fetched or processed data, reuse it or run python_exec \
to query it — do not ask the user to re-provide it.
- For data analysis follow-ups (filtering, counting, aggregating), use python_exec to \
compute the answer rather than trying to parse raw text in context.

Tool selection guide (use the right tool for the job):
- Read a file: use Read (not bash cat/head/tail)
- Edit a file: use Edit for targeted changes, Write only for new files or complete rewrites
- Search file contents: use Grep (not bash grep/rg)
- Find files by name/pattern: use Glob (not bash find/ls)
- Run Python code: use PythonExec (not bash python -c) — avoids shell quoting issues
- Fetch a web page: use WebFetch (external URLs only, never localhost)
- Search the web: use WebSearch, then WebFetch to read full pages from results
- Run shell commands (git, curl, npm, etc.): use Bash
- Multi-step complex tasks: use Agent to spawn a sub-agent
- Iterative quality improvement: use EvalOptimize (generator-evaluator loop)
- Data analysis: use LoadData to load files, then QueryData for pandas operations, \
or CreateChart for quick visualizations
When multiple tools could work, prefer the specialized tool over Bash — specialized tools \
provide better structured output and are safer (sandboxed, validated).

Tool usage rules:
- WebFetch is ONLY for external HTTP/HTTPS URLs on the public internet. NEVER use WebFetch \
for local files (file://), localhost, or 127.0.0.1 — it will be blocked. Use Read or \
Bash with curl to access local files and local services.
- Scheduled tasks are managed via the /schedule command, NOT via the OS task scheduler. \
Use `/schedule list` to view (shows short IDs), `/schedule delete <name|id>` to remove, \
`/schedule enable|disable <name|id>` to toggle, `/schedule run <name|id>` to trigger immediately. \
You can reference schedules by name or short ID prefix. Never suggest Windows Task Scheduler, \
cron, or other OS-level scheduling — all scheduling is handled internally.
- For local API endpoints or services, use Bash with curl, not WebFetch.

Error handling:
- If a tool returns an error, read the error message carefully before retrying.
- If a file doesn't exist, check the path with Glob before assuming it was deleted.
- If Edit fails with "not found", verify the exact content with Read first.
- If WebFetch fails, try WebSearch to find an alternative URL.
- Do not retry the same failing command more than twice — diagnose the issue first.

Model: {settings.model}
"""


def build_system_prompt(
    skill_prompt: str | None = None,
    memory_context: str | None = None,
    user_system_prompt: str | None = None,
    project_config: str | None = None,
    rules_text: str | None = None,
    outputs_dir: str | None = None,
    available_agents: list[tuple[str, str]] | None = None,
    allowed_skills: list[str] | None = None,
    routing_hint: str | None = None,
) -> str:
    """Compose the final system prompt for a chat turn.

    ``allowed_skills`` narrows the "Available Skills" and "Auto-Activated
    Skills" sections to only those names (matching both plain and
    ``plugin:skill`` forms). Used by agent runs so an agent only sees the
    skills it composes, not every globally-registered skill. ``None``
    preserves the chat-default of exposing all skills.

    ``routing_hint`` is a per-turn nudge written by the chat handler when
    a deterministic intent matcher (``match_agent_by_intent``) thinks the
    user's message should be delegated to a specific agent. It rides at
    the very top of the prompt so the model sees it before any other
    instruction. The matcher itself never dispatches -- the model still
    decides whether to follow the hint.

    Deferred imports for skills/commands avoid a load-order issue: this module
    is imported from core/, but the registries live above core/ and are
    populated at server startup.
    """
    from skills.registry import list_skills
    from commands.registry import list_commands

    base = BASE_SYSTEM_PROMPT
    if outputs_dir:
        base = base.replace(str(DEFAULT_OUTPUTS_DIR), str(outputs_dir))
    parts = [base]

    # Today's date -- evaluated per turn so a long-running server doesn't
    # serve stale dates. Without this, skills that timestamp output (e.g.
    # person-research's `Prepared:` line and `<lastname>-<co>-<YYYYMMDD>`
    # filename) silently default to the model's training-cutoff date.
    parts.append(f"\nToday's date: {_date.today().isoformat()}")

    # Per-turn routing nudge -- highest priority.
    if routing_hint:
        parts.append(f"\n## TURN ROUTING -- read first\n{routing_hint}")

    # Available agents block -- moved above other sections so the model
    # absorbs specialist routing rules before tool guides or memory.
    if available_agents:
        agent_lines = [f"- **{name}** -- {desc}" for name, desc in available_agents]
        parts.append(
            "\n## ROUTING -- read before responding\n"
            "The user has configured the specialist agents below. When an "
            "incoming request matches one of their descriptions, you MUST "
            "delegate to that agent via the `Agent` tool rather than handle "
            "the task inline with raw tools like WebSearch, WebFetch, Read, "
            "or Write. The user set these agents up precisely so you would "
            "route to them -- bypassing them produces inconsistent, "
            "unstructured output.\n\n"
            "Worked example -- if a user has an agent `person-research` "
            "described as \"Research a person; find LinkedIn, email, recent "
            "news\" and types \"research John at Acme\":\n"
            "  CORRECT: call `Agent(agent_type=\"person-research\", "
            "prompt=\"Research John at Acme\")`.\n"
            "  WRONG: call WebSearch / WebFetch / Read directly to do the "
            "research yourself.\n\n"
            "To delegate: call `Agent(agent_type=\"<agent-name>\", "
            "prompt=\"<full task details including the original user "
            "message and any context you have>\")`. Do not paraphrase or "
            "summarize the task before handing off -- pass it verbatim.\n\n"
            "Only handle a request inline if NO listed agent fits. When in "
            "doubt between two agents, pick the more specific one.\n\n"
            "**Agent teams pattern.** For tasks that benefit from multiple "
            "specialists (\"review this with your team\", \"get a second "
            "opinion\"), spawn each in parallel by emitting several "
            "`Agent(...)` calls in a single turn. Each call returns an "
            "`agent_id`. To follow up with one specialist without "
            "respawning -- e.g. ask a clarifying question or feed back "
            "another specialist's finding -- call "
            "`SendMessage(to=\"<agent_id>\", message=\"<next prompt>\")`. "
            "The subagent keeps its system prompt, tools, and full "
            "sub-conversation history. Combine outputs at the end and "
            "respond to the user.\n\n"
            "Agents available to this user:\n" + "\n".join(agent_lines)
        )

    if project_config:
        parts.append(f"\n## Project Instructions\n{project_config}")
    if rules_text:
        parts.append(f"\n## Project Rules\n{rules_text}")
    if user_system_prompt:
        parts.append(f"\n## User Instructions\n{user_system_prompt}")
    if skill_prompt:
        parts.append(f"\n## Active Skill\n{skill_prompt}")
    if memory_context:
        parts.append(
            f"\n## Your Memory\nThe following are things you remember about this user:\n{memory_context}"
        )

    skills = list_skills()
    if allowed_skills is not None:
        allowed = {a.strip() for a in allowed_skills if a and a.strip()}
        skills = [
            s for s in skills
            if s.name in allowed or s.name.split(":", 1)[-1] in allowed
        ]
    visible_skills = [s for s in skills if s.user_invocable and not s.disable_model_invocation]
    auto_activated = [s for s in skills if not s.disable_model_invocation]

    if visible_skills:
        lines = []
        for s in visible_skills:
            hint = f" {s.argument_hint}" if s.argument_hint else ""
            lines.append(f"- /{s.name}{hint}: {s.description}")
        parts.append("\n## Available Skills\nUsers can invoke these with /name:\n" + "\n".join(lines))

    if auto_activated:
        names = ", ".join(s.name for s in auto_activated)
        parts.append(
            f"\n## Auto-Activated Skills\nThese activate automatically based on conversation context: {names}"
        )

    commands = list_commands()
    if commands:
        cmd_lines = []
        for c in commands:
            hint = f" {c.argument_hint}" if c.argument_hint else ""
            cmd_lines.append(f"- /{c.name}{hint}: {c.description}")
        parts.append("\n## Available Commands\n" + "\n".join(cmd_lines))

    return "\n".join(parts)
