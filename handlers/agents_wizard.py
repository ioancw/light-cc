"""Conversational wizard for ``/agents create [<name>]`` (W1 of CC parity plan).

The wizard is a per-session state machine stored on the WebSocket
connection. Each user message advances one step; ``cancel`` aborts,
``back`` rewinds, ``skip`` (or empty) skips the current optional step.
On confirm, an ``AGENT.md`` file is written and the new definition
synced to the DB so ``@agent-<name>`` dispatches immediately.

The wizard intentionally lives in application code (built-in territory),
not as a skill -- it needs to write files and manage state. Per the plan,
every CC frontmatter field is collected and persisted to YAML even if
Light CC's runner doesn't honour all of them yet, so AGENT.md files stay
forward-compatible with CC.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.session import connection_get, connection_set

logger = logging.getLogger(__name__)


# kebab-case validator -- matches CC and our own ``_VALID_NAME_RE`` in
# skills/loader.py. Constraints: lowercase alphanumerics + hyphens, no
# leading/trailing hyphen, max 64 chars, no double hyphens.
_VALID_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def _is_kebab(name: str) -> bool:
    if not name or len(name) > 64 or "--" in name:
        return False
    return bool(_VALID_NAME_RE.match(name))


_PERMISSION_MODES = ("default", "acceptEdits", "plan", "bypassPermissions")
_ISOLATION_MODES = ("", "worktree")


def _parse_csv(s: str) -> list[str]:
    """Tolerant CSV parser used for tools/disallowedTools/mcpServers/skills.

    Accepts comma- or whitespace-separated input.
    """
    if not s.strip():
        return []
    if "," in s:
        return [t.strip() for t in s.split(",") if t.strip()]
    return [t for t in s.split() if t]


def _parse_bool(s: str) -> bool | None:
    v = s.strip().lower()
    if v in ("true", "yes", "y", "on", "1"):
        return True
    if v in ("false", "no", "n", "off", "0"):
        return False
    return None


@dataclass
class _Step:
    """One conversational step.

    ``ask`` returns the prompt shown to the user.
    ``consume`` validates the user's reply, mutates ``state.data`` if
    accepted, and returns either the next step's index (advance) or an
    error string (re-ask current step). Optional steps treat empty/``skip``
    as a clean skip; required steps reject them.
    """

    key: str
    ask: Callable[["AgentWizardState"], str]
    consume: Callable[["AgentWizardState", str], int | str]
    optional: bool = False
    label: str = ""  # display label for the review screen


@dataclass
class AgentWizardState:
    """Per-session wizard state. Persisted via ``connection_set``."""

    user_id: str
    step_index: int = 0
    data: dict[str, Any] = field(default_factory=dict)
    awaiting_overwrite: bool = False  # set when name conflicts at confirm time


# ── Steps ───────────────────────────────────────────────────────────────

def _next(state: AgentWizardState) -> int:
    return state.step_index + 1


def _step_name() -> _Step:
    def ask(s: AgentWizardState) -> str:
        return (
            "**Step 1 / 13 — name**\n\n"
            "What should the agent be called? Use kebab-case "
            "(lowercase, hyphens, e.g. `person-research`)."
        )

    def consume(s: AgentWizardState, text: str) -> int | str:
        name = text.strip()
        if not _is_kebab(name):
            return (
                "Name must be kebab-case: lowercase letters, digits, and "
                "hyphens, no leading/trailing or double hyphens, max 64 "
                "chars. Try again, or `cancel` to abort."
            )
        s.data["name"] = name
        return _next(s)

    return _Step("name", ask, consume, label="Name")


def _step_description() -> _Step:
    def ask(s: AgentWizardState) -> str:
        return (
            "**Step 2 / 13 — description**\n\n"
            "One-line description shown in `/agents` and used by intent "
            "matching. (e.g. *\"Research a person across the web and write "
            "a structured profile.\"*)"
        )

    def consume(s: AgentWizardState, text: str) -> int | str:
        desc = text.strip()
        if not desc:
            return "Description is required. What does this agent do?"
        s.data["description"] = desc
        return _next(s)

    return _Step("description", ask, consume, label="Description")


def _optional_skip(text: str) -> bool:
    """``skip``, ``-``, or empty means: leave this optional field unset."""
    return text.strip().lower() in ("", "skip", "-", "none")


def _step_model(default_model: str) -> _Step:
    def ask(s: AgentWizardState) -> str:
        return (
            "**Step 3 / 13 — model** _(optional)_\n\n"
            f"Override the agent's model? Default: `{default_model}`. "
            "Type a model id (e.g. `claude-opus-4-7`) or `skip` to inherit."
        )

    def consume(s: AgentWizardState, text: str) -> int | str:
        if _optional_skip(text):
            return _next(s)
        s.data["model"] = text.strip()
        return _next(s)

    return _Step("model", ask, consume, optional=True, label="Model")


def _step_tools() -> _Step:
    def ask(s: AgentWizardState) -> str:
        from tools.registry import get_all_tool_schemas
        tool_names = [t["name"] for t in get_all_tool_schemas()]
        sample = ", ".join(tool_names[:8])
        return (
            "**Step 4 / 13 — tools** _(optional)_\n\n"
            f"Comma-separated allow-list of tools. Available tools "
            f"include: `{sample}`{'...' if len(tool_names) > 8 else ''}. "
            "`skip` to inherit all."
        )

    def consume(s: AgentWizardState, text: str) -> int | str:
        if _optional_skip(text):
            return _next(s)
        tools = _parse_csv(text)
        if not tools:
            return "Couldn't parse a tool list. Try `Read, Edit, Bash` or `skip`."
        s.data["tools"] = tools
        return _next(s)

    return _Step("tools", ask, consume, optional=True, label="Tools")


def _step_disallowed_tools() -> _Step:
    def ask(s: AgentWizardState) -> str:
        return (
            "**Step 5 / 13 — disallowed tools** _(optional)_\n\n"
            "Comma-separated tool blocklist applied on top of inherited "
            "defaults. `skip` if you have no blocklist."
        )

    def consume(s: AgentWizardState, text: str) -> int | str:
        if _optional_skip(text):
            return _next(s)
        items = _parse_csv(text)
        if items:
            s.data["disallowedTools"] = items
        return _next(s)

    return _Step("disallowedTools", ask, consume, optional=True, label="Disallowed tools")


def _step_skills() -> _Step:
    def ask(s: AgentWizardState) -> str:
        return (
            "**Step 6 / 13 — skills** _(optional)_\n\n"
            "Comma-separated skill names this agent should be able to "
            "compose (via the `Skill` tool). `skip` to inherit all."
        )

    def consume(s: AgentWizardState, text: str) -> int | str:
        if _optional_skip(text):
            return _next(s)
        items = _parse_csv(text)
        if items:
            s.data["skills"] = items
        return _next(s)

    return _Step("skills", ask, consume, optional=True, label="Skills")


def _step_mcp_servers() -> _Step:
    def ask(s: AgentWizardState) -> str:
        return (
            "**Step 7 / 13 — MCP servers** _(optional)_\n\n"
            "Comma-separated names of MCP servers the agent should connect "
            "to. `skip` if none."
        )

    def consume(s: AgentWizardState, text: str) -> int | str:
        if _optional_skip(text):
            return _next(s)
        items = _parse_csv(text)
        if items:
            s.data["mcpServers"] = items
        return _next(s)

    return _Step("mcpServers", ask, consume, optional=True, label="MCP servers")


def _step_permission_mode() -> _Step:
    def ask(s: AgentWizardState) -> str:
        return (
            "**Step 8 / 13 — permission mode** _(optional)_\n\n"
            "How should tool-permission prompts behave for this agent? "
            "Options: `default`, `acceptEdits`, `plan`, `bypassPermissions`. "
            "`skip` to use `default`."
        )

    def consume(s: AgentWizardState, text: str) -> int | str:
        if _optional_skip(text):
            return _next(s)
        mode = text.strip()
        if mode not in _PERMISSION_MODES:
            return (
                f"Unknown mode `{mode}`. Pick one of: "
                + ", ".join(f"`{m}`" for m in _PERMISSION_MODES)
                + " or `skip`."
            )
        s.data["permissionMode"] = mode
        return _next(s)

    return _Step("permissionMode", ask, consume, optional=True, label="Permission mode")


def _step_isolation() -> _Step:
    def ask(s: AgentWizardState) -> str:
        return (
            "**Step 9 / 13 — isolation** _(optional)_\n\n"
            "Run the agent in an isolated git worktree? Type `worktree` to "
            "enable, `skip` to share the main cwd."
        )

    def consume(s: AgentWizardState, text: str) -> int | str:
        if _optional_skip(text):
            return _next(s)
        choice = text.strip().lower()
        if choice not in _ISOLATION_MODES or choice == "":
            return "Type `worktree` or `skip`."
        s.data["isolation"] = choice
        return _next(s)

    return _Step("isolation", ask, consume, optional=True, label="Isolation")


def _step_background() -> _Step:
    def ask(s: AgentWizardState) -> str:
        return (
            "**Step 10 / 13 — background** _(optional)_\n\n"
            "Should the agent run in the background (off the main turn)? "
            "Type `yes` / `no` / `skip` (default: no)."
        )

    def consume(s: AgentWizardState, text: str) -> int | str:
        if _optional_skip(text):
            return _next(s)
        b = _parse_bool(text)
        if b is None:
            return "Reply `yes` or `no` (or `skip`)."
        if b:
            s.data["background"] = True
        return _next(s)

    return _Step("background", ask, consume, optional=True, label="Background")


def _step_initial_prompt() -> _Step:
    def ask(s: AgentWizardState) -> str:
        return (
            "**Step 11 / 13 — initial prompt** _(optional)_\n\n"
            "Pre-seeded user-like message injected at every dispatch. Useful "
            "to anchor the agent on a recurring task framing. `skip` if not "
            "needed."
        )

    def consume(s: AgentWizardState, text: str) -> int | str:
        if _optional_skip(text):
            return _next(s)
        s.data["initialPrompt"] = text.strip()
        return _next(s)

    return _Step("initialPrompt", ask, consume, optional=True, label="Initial prompt")


def _step_color() -> _Step:
    def ask(s: AgentWizardState) -> str:
        return (
            "**Step 12 / 13 — color** _(optional)_\n\n"
            "Optional UI hint passed through to clients (CC accepts e.g. "
            "`blue`, `green`). `skip` if you don't care."
        )

    def consume(s: AgentWizardState, text: str) -> int | str:
        if _optional_skip(text):
            return _next(s)
        s.data["color"] = text.strip()
        return _next(s)

    return _Step("color", ask, consume, optional=True, label="Color")


def _step_system_prompt() -> _Step:
    def ask(s: AgentWizardState) -> str:
        return (
            "**Step 13 / 13 — system prompt**\n\n"
            "The agent's instructions, in your own words. Multi-line OK -- "
            "send the full body as one message. This is the `system_prompt` "
            "the agent runs with."
        )

    def consume(s: AgentWizardState, text: str) -> int | str:
        body = text.strip()
        if not body:
            return "System prompt is required. Send the body as one message."
        s.data["system_prompt"] = body
        return _next(s)

    return _Step("system_prompt", ask, consume, label="System prompt")


def _step_review() -> _Step:
    def ask(s: AgentWizardState) -> str:
        d = s.data
        lines = ["**Review** — please confirm:\n"]
        for key, label in (
            ("name", "Name"),
            ("description", "Description"),
            ("model", "Model"),
            ("tools", "Tools"),
            ("disallowedTools", "Disallowed tools"),
            ("skills", "Skills"),
            ("mcpServers", "MCP servers"),
            ("permissionMode", "Permission mode"),
            ("isolation", "Isolation"),
            ("background", "Background"),
            ("initialPrompt", "Initial prompt"),
            ("color", "Color"),
        ):
            if key in d:
                v = d[key]
                if isinstance(v, list):
                    v = ", ".join(v)
                lines.append(f"- **{label}:** {v}")
        body = d.get("system_prompt", "")
        body_preview = body if len(body) <= 200 else body[:200] + "..."
        lines.append(f"- **System prompt preview:** {body_preview}")
        lines.append("\nReply `confirm` to write the file, `back` to revise the previous step, or `cancel` to abort.")
        return "\n".join(lines)

    def consume(s: AgentWizardState, text: str) -> int | str:
        # The agent_handler intercepts confirm/cancel/back at a higher level;
        # if we ever get a raw text answer here it's an error.
        return "Reply `confirm`, `back`, or `cancel`."

    return _Step("review", ask, consume, label="Review")


def _build_steps(default_model: str) -> list[_Step]:
    return [
        _step_name(),
        _step_description(),
        _step_model(default_model),
        _step_tools(),
        _step_disallowed_tools(),
        _step_skills(),
        _step_mcp_servers(),
        _step_permission_mode(),
        _step_isolation(),
        _step_background(),
        _step_initial_prompt(),
        _step_color(),
        _step_system_prompt(),
        _step_review(),
    ]


# ── Public API ──────────────────────────────────────────────────────────

_WIZARD_KEY = "agents_wizard"


@dataclass
class WizardOutcome:
    """Returned to ``handle_user_message`` after the wizard processes input."""

    text: str
    finished: bool = False         # wizard fully done (file written or cancelled)
    agents_updated: bool = False    # frontend roster needs refresh


def is_wizard_active(session_id: str) -> bool:
    return connection_get(session_id, _WIZARD_KEY) is not None


def _save(session_id: str, state: AgentWizardState) -> None:
    connection_set(session_id, _WIZARD_KEY, state)


def _clear(session_id: str) -> None:
    connection_set(session_id, _WIZARD_KEY, None)


def _default_model_for_session(session_id: str) -> str:
    from core.config import settings
    return connection_get(session_id, "model") or settings.model


def start_wizard(session_id: str, user_id: str, name_hint: str = "") -> str:
    """Initialise a wizard for this session and return the first prompt.

    ``name_hint`` (from ``/agents create <name>``) pre-fills the name step
    and skips it on success; on validation failure the wizard asks for
    ``name`` interactively.
    """
    state = AgentWizardState(user_id=user_id)
    default_model = _default_model_for_session(session_id)
    steps = _build_steps(default_model)

    if name_hint and _is_kebab(name_hint):
        state.data["name"] = name_hint
        state.step_index = 1  # skip name step
        _save(session_id, state)
        return (
            f"Starting `/agents create {name_hint}`. Type `cancel` at any "
            f"time to abort, `back` to revise the previous step.\n\n"
            + steps[1].ask(state)
        )

    _save(session_id, state)
    return (
        "Starting `/agents create`. Type `cancel` at any time to abort, "
        "`back` to revise the previous step.\n\n"
        + steps[0].ask(state)
    )


def cancel_wizard(session_id: str) -> str:
    _clear(session_id)
    return "Cancelled. No agent file was written."


async def handle_wizard_input(
    session_id: str,
    user_id: str,
    text: str,
    project_root: Path,
) -> WizardOutcome:
    """Advance the wizard one step using ``text`` as the user's reply."""
    state: AgentWizardState | None = connection_get(session_id, _WIZARD_KEY)
    if state is None:
        # Defensive: caller should have checked ``is_wizard_active`` first.
        return WizardOutcome(text="No agent-creation wizard is in progress.", finished=True)

    raw = text.strip()
    lower = raw.lower()
    default_model = _default_model_for_session(session_id)
    steps = _build_steps(default_model)
    current = steps[state.step_index]

    # ── Universal commands ──
    if lower in ("cancel", "/cancel", "quit", "/quit", "abort"):
        return WizardOutcome(text=cancel_wizard(session_id), finished=True)

    if lower == "back":
        if state.awaiting_overwrite:
            # Step out of the overwrite confirm and back into review.
            state.awaiting_overwrite = False
            _save(session_id, state)
            return WizardOutcome(text=steps[-1].ask(state))
        if state.step_index == 0:
            return WizardOutcome(text="Already at the first step. Try `cancel` to abort.")
        # If the user originally supplied a name inline, we skipped step 0;
        # ``back`` from step 1 should still land on step 0 to allow renaming.
        state.step_index -= 1
        _save(session_id, state)
        return WizardOutcome(text=steps[state.step_index].ask(state))

    # ── Overwrite confirmation (special branch off the review step) ──
    if state.awaiting_overwrite:
        b = _parse_bool(raw)
        if b is None:
            return WizardOutcome(text="Reply `yes` to overwrite, `no` to abort, or `back` to revise.")
        if not b:
            return WizardOutcome(text=cancel_wizard(session_id), finished=True)
        # Confirmed overwrite -- proceed with write
        return await _finalize(session_id, state, project_root, overwrite=True)

    # ── Review-step controls ──
    if current.key == "review":
        if lower == "confirm":
            return await _finalize(session_id, state, project_root, overwrite=False)
        # Anything else at review re-asks the review prompt with hint.
        return WizardOutcome(text="Reply `confirm`, `back`, or `cancel`.")

    # ── Normal step advance ──
    result = current.consume(state, raw)
    if isinstance(result, str):
        return WizardOutcome(text=result)  # validation failed; re-ask

    state.step_index = result
    _save(session_id, state)
    return WizardOutcome(text=steps[state.step_index].ask(state))


async def _finalize(
    session_id: str,
    state: AgentWizardState,
    project_root: Path,
    *,
    overwrite: bool,
) -> WizardOutcome:
    """Write AGENT.md and sync the row to the DB."""
    from core.agent_loader import AgentDef, sync_agent_defs_to_db, write_agent_def
    from core.config import settings

    d = state.data
    name = d["name"]

    # Resolve agents_dir relative to project root (uses first configured dir).
    agents_dir_setting = settings.paths.agents_dirs[0] if settings.paths.agents_dirs else "agents"
    agents_dir = Path(agents_dir_setting)
    if not agents_dir.is_absolute():
        agents_dir = project_root / agents_dir

    # Pre-flight: prompt for overwrite the first time we discover a clash.
    target = agents_dir / name / "AGENT.md"
    if target.exists() and not overwrite:
        state.awaiting_overwrite = True
        _save(session_id, state)
        return WizardOutcome(
            text=(
                f"An `AGENT.md` already exists at `{target}`. "
                "Overwrite it? Reply `yes` / `no` / `back`."
            ),
        )

    def_ = AgentDef(
        name=name,
        description=d["description"],
        system_prompt=d["system_prompt"],
        model=d.get("model"),
        tools=d.get("tools"),
        skills=d.get("skills"),
    )
    extras = {
        k: v for k, v in d.items()
        if k in (
            "disallowedTools", "mcpServers", "permissionMode",
            "isolation", "background", "initialPrompt", "color",
        )
    }

    try:
        path = write_agent_def(def_, agents_dir, overwrite=overwrite, extra_frontmatter=extras)
    except Exception as e:
        logger.exception("Failed to write AGENT.md")
        _clear(session_id)
        return WizardOutcome(
            text=f"Failed to write the agent file: {e}",
            finished=True,
        )

    try:
        await sync_agent_defs_to_db([def_], state.user_id, source_label="user")
    except Exception as e:
        logger.exception("Failed to sync agent to DB")
        _clear(session_id)
        return WizardOutcome(
            text=(
                f"Wrote `{path}` but failed to register the agent in the "
                f"database: {e}. Run `/reload` after fixing the issue."
            ),
            finished=True,
            agents_updated=True,
        )

    _clear(session_id)
    return WizardOutcome(
        text=(
            f"**Agent `{name}` created.**\n\n"
            f"- File: `{path}`\n"
            f"- Dispatch: `@agent-{name} <prompt>`\n\n"
            f"It's already loaded -- try it now."
        ),
        finished=True,
        agents_updated=True,
    )
