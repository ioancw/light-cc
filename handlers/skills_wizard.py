"""Conversational wizard for ``/skills create [<name>]`` (W2 of CC parity plan).

Mirrors ``handlers/agents_wizard.py``: a per-session state machine on the
WS connection drives the user through the skill frontmatter step by step.
On confirm an ``SKILL.md`` file is written and ``reload_skills()`` runs so
``/<name>`` is available immediately in the slash menu.

Design parity with the agents wizard:
- Universal commands ``cancel`` / ``back`` / ``skip`` work the same way.
- Optional fields treat empty / ``skip`` / ``-`` / ``none`` as "leave unset".
- Pre-flight overwrite check: name conflict prompts ``yes`` / ``no``
  before stomping the file.
- Frontmatter pass-through fields (``shell``, ``model``, ``effort``,
  ``paths``) are persisted verbatim even when Light CC's runner does not
  yet honour them, keeping SKILL.md files CC-portable.

The wizard intentionally lives in application code -- it writes files and
manages multi-turn state, classic built-in territory.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.session import connection_get, connection_set

logger = logging.getLogger(__name__)


# kebab-case validator -- mirrors ``_VALID_NAME_RE`` in skills/loader.py.
_VALID_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def _is_kebab(name: str) -> bool:
    if not name or len(name) > 64 or "--" in name:
        return False
    return bool(_VALID_NAME_RE.match(name))


_CONTEXT_MODES = ("", "fork")


def _parse_csv(s: str) -> list[str]:
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


def _optional_skip(text: str) -> bool:
    return text.strip().lower() in ("", "skip", "-", "none")


@dataclass
class _Step:
    key: str
    ask: Callable[["SkillWizardState"], str]
    consume: Callable[["SkillWizardState", str], int | str]
    optional: bool = False
    label: str = ""


@dataclass
class SkillWizardState:
    user_id: str
    step_index: int = 0
    data: dict[str, Any] = field(default_factory=dict)
    awaiting_overwrite: bool = False


def _next(state: SkillWizardState) -> int:
    return state.step_index + 1


def _step_name() -> _Step:
    def ask(s: SkillWizardState) -> str:
        return (
            "**Step 1 / 9 — name**\n\n"
            "What should the skill be called? Use kebab-case "
            "(lowercase, hyphens, e.g. `git-workflow`). It becomes "
            "`/<name>` in chat."
        )

    def consume(s: SkillWizardState, text: str) -> int | str:
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
    def ask(s: SkillWizardState) -> str:
        return (
            "**Step 2 / 9 — description**\n\n"
            "One-line description shown in the `/` autocomplete picker and "
            "used by intent matching. Describe *when* this skill should be "
            "invoked, not *how*."
        )

    def consume(s: SkillWizardState, text: str) -> int | str:
        desc = text.strip()
        if not desc:
            return "Description is required. What does this skill do, and when should it fire?"
        s.data["description"] = desc
        return _next(s)

    return _Step("description", ask, consume, label="Description")


def _step_argument_hint() -> _Step:
    def ask(s: SkillWizardState) -> str:
        return (
            "**Step 3 / 9 — argument hint** _(optional)_\n\n"
            "Short string shown in the `/` picker -- e.g. `[ticker] [since]` "
            "or `<file>`. `skip` if the skill takes no arguments."
        )

    def consume(s: SkillWizardState, text: str) -> int | str:
        if _optional_skip(text):
            return _next(s)
        s.data["argument_hint"] = text.strip()
        return _next(s)

    return _Step("argument_hint", ask, consume, optional=True, label="Argument hint")


def _step_tools() -> _Step:
    def ask(s: SkillWizardState) -> str:
        from tools.registry import get_all_tool_schemas
        tool_names = [t["name"] for t in get_all_tool_schemas()]
        sample = ", ".join(tool_names[:8])
        return (
            "**Step 4 / 9 — allowed tools** _(optional)_\n\n"
            f"Comma-separated allow-list of tools the skill is permitted "
            f"to use. Available tools include: `{sample}`"
            f"{'...' if len(tool_names) > 8 else ''}. `skip` to inherit all."
        )

    def consume(s: SkillWizardState, text: str) -> int | str:
        if _optional_skip(text):
            return _next(s)
        items = _parse_csv(text)
        if not items:
            return "Couldn't parse a tool list. Try `Read, Edit, Bash` or `skip`."
        s.data["tools"] = items
        return _next(s)

    return _Step("tools", ask, consume, optional=True, label="Allowed tools")


def _step_user_invocable() -> _Step:
    def ask(s: SkillWizardState) -> str:
        return (
            "**Step 5 / 9 — user-invocable** _(optional)_\n\n"
            "Should `/<name>` appear in the autocomplete picker so the user "
            "can fire it directly? Reply `yes` (default) / `no` / `skip`."
        )

    def consume(s: SkillWizardState, text: str) -> int | str:
        if _optional_skip(text):
            return _next(s)
        b = _parse_bool(text)
        if b is None:
            return "Reply `yes` or `no` (or `skip`)."
        # Only persist if it diverges from the SkillDef default (True).
        if b is False:
            s.data["user_invocable"] = False
        return _next(s)

    return _Step("user_invocable", ask, consume, optional=True, label="User-invocable")


def _step_disable_model_invocation() -> _Step:
    def ask(s: SkillWizardState) -> str:
        return (
            "**Step 6 / 9 — disable model invocation** _(optional)_\n\n"
            "Should the model be PREVENTED from auto-invoking this skill via "
            "intent matching? Useful for destructive workflows. Reply `yes` "
            "(disable auto-invoke) / `no` (default — model can match) / `skip`."
        )

    def consume(s: SkillWizardState, text: str) -> int | str:
        if _optional_skip(text):
            return _next(s)
        b = _parse_bool(text)
        if b is None:
            return "Reply `yes` or `no` (or `skip`)."
        if b is True:
            s.data["disable_model_invocation"] = True
        return _next(s)

    return _Step(
        "disable_model_invocation", ask, consume, optional=True,
        label="Disable model invocation",
    )


def _step_context() -> _Step:
    def ask(s: SkillWizardState) -> str:
        return (
            "**Step 7 / 9 — context** _(optional)_\n\n"
            "Run the skill in an isolated sub-conversation (`fork`)? Useful "
            "for skills that should not pollute the main chat history. "
            "Reply `fork` to enable, `skip` to share the main context."
        )

    def consume(s: SkillWizardState, text: str) -> int | str:
        if _optional_skip(text):
            return _next(s)
        choice = text.strip().lower()
        if choice not in _CONTEXT_MODES or choice == "":
            return "Type `fork` or `skip`."
        s.data["context"] = choice
        return _next(s)

    return _Step("context", ask, consume, optional=True, label="Context")


def _step_prompt() -> _Step:
    def ask(s: SkillWizardState) -> str:
        return (
            "**Step 8 / 9 — skill body**\n\n"
            "The skill's instructions, in your own words. Multi-line OK -- "
            "send the full body as one message. This is the markdown body "
            "of the SKILL.md file (everything after the frontmatter)."
        )

    def consume(s: SkillWizardState, text: str) -> int | str:
        body = text.strip()
        if not body:
            return "The body is required. Send it as one message."
        s.data["prompt"] = body
        return _next(s)

    return _Step("prompt", ask, consume, label="Body")


def _step_review() -> _Step:
    def ask(s: SkillWizardState) -> str:
        d = s.data
        lines = ["**Review** — please confirm:\n"]
        for key, label in (
            ("name", "Name"),
            ("description", "Description"),
            ("argument_hint", "Argument hint"),
            ("tools", "Allowed tools"),
            ("user_invocable", "User-invocable"),
            ("disable_model_invocation", "Disable model invocation"),
            ("context", "Context"),
        ):
            if key in d:
                v = d[key]
                if isinstance(v, list):
                    v = ", ".join(v)
                lines.append(f"- **{label}:** {v}")
        body = d.get("prompt", "")
        body_preview = body if len(body) <= 200 else body[:200] + "..."
        lines.append(f"- **Body preview:** {body_preview}")
        lines.append("\nReply `confirm` to write the file, `back` to revise the previous step, or `cancel` to abort.")
        return "\n".join(lines)

    def consume(s: SkillWizardState, text: str) -> int | str:
        return "Reply `confirm`, `back`, or `cancel`."

    return _Step("review", ask, consume, label="Review")


def _build_steps() -> list[_Step]:
    return [
        _step_name(),
        _step_description(),
        _step_argument_hint(),
        _step_tools(),
        _step_user_invocable(),
        _step_disable_model_invocation(),
        _step_context(),
        _step_prompt(),
        _step_review(),
    ]


# ── Public API ──────────────────────────────────────────────────────────

_WIZARD_KEY = "skills_wizard"


@dataclass
class WizardOutcome:
    text: str
    finished: bool = False
    skills_updated: bool = False


def is_wizard_active(session_id: str) -> bool:
    return connection_get(session_id, _WIZARD_KEY) is not None


def _save(session_id: str, state: SkillWizardState) -> None:
    connection_set(session_id, _WIZARD_KEY, state)


def _clear(session_id: str) -> None:
    connection_set(session_id, _WIZARD_KEY, None)


def start_wizard(session_id: str, user_id: str, name_hint: str = "") -> str:
    """Initialise a wizard and return the first prompt."""
    state = SkillWizardState(user_id=user_id)
    steps = _build_steps()

    if name_hint and _is_kebab(name_hint):
        state.data["name"] = name_hint
        state.step_index = 1
        _save(session_id, state)
        return (
            f"Starting `/skills create {name_hint}`. Type `cancel` at any "
            f"time to abort, `back` to revise the previous step.\n\n"
            + steps[1].ask(state)
        )

    _save(session_id, state)
    return (
        "Starting `/skills create`. Type `cancel` at any time to abort, "
        "`back` to revise the previous step.\n\n"
        + steps[0].ask(state)
    )


def cancel_wizard(session_id: str) -> str:
    _clear(session_id)
    return "Cancelled. No skill file was written."


async def handle_wizard_input(
    session_id: str,
    user_id: str,
    text: str,
    project_root: Path,
) -> WizardOutcome:
    """Advance the wizard one step using ``text`` as the user's reply."""
    state: SkillWizardState | None = connection_get(session_id, _WIZARD_KEY)
    if state is None:
        return WizardOutcome(text="No skill-creation wizard is in progress.", finished=True)

    raw = text.strip()
    lower = raw.lower()
    steps = _build_steps()
    current = steps[state.step_index]

    if lower in ("cancel", "/cancel", "quit", "/quit", "abort"):
        return WizardOutcome(text=cancel_wizard(session_id), finished=True)

    if lower == "back":
        if state.awaiting_overwrite:
            state.awaiting_overwrite = False
            _save(session_id, state)
            return WizardOutcome(text=steps[-1].ask(state))
        if state.step_index == 0:
            return WizardOutcome(text="Already at the first step. Try `cancel` to abort.")
        state.step_index -= 1
        _save(session_id, state)
        return WizardOutcome(text=steps[state.step_index].ask(state))

    if state.awaiting_overwrite:
        b = _parse_bool(raw)
        if b is None:
            return WizardOutcome(text="Reply `yes` to overwrite, `no` to abort, or `back` to revise.")
        if not b:
            return WizardOutcome(text=cancel_wizard(session_id), finished=True)
        return await _finalize(session_id, state, project_root, overwrite=True)

    if current.key == "review":
        if lower == "confirm":
            return await _finalize(session_id, state, project_root, overwrite=False)
        return WizardOutcome(text="Reply `confirm`, `back`, or `cancel`.")

    result = current.consume(state, raw)
    if isinstance(result, str):
        return WizardOutcome(text=result)

    state.step_index = result
    _save(session_id, state)
    return WizardOutcome(text=steps[state.step_index].ask(state))


async def _finalize(
    session_id: str,
    state: SkillWizardState,
    project_root: Path,
    *,
    overwrite: bool,
) -> WizardOutcome:
    """Write SKILL.md and refresh the registry."""
    from core.config import settings
    from core.models import SkillDef
    from skills.loader import parse_skill_file, write_skill_def
    from skills.registry import register_skill

    d = state.data
    name = d["name"]

    # Resolve skills_dir relative to project root (uses first configured dir).
    skills_dir_setting = settings.paths.skills_dirs[0] if settings.paths.skills_dirs else "skills"
    skills_dir = Path(skills_dir_setting).expanduser()
    if not skills_dir.is_absolute():
        skills_dir = project_root / skills_dir

    target = skills_dir / name / "SKILL.md"
    if target.exists() and not overwrite:
        state.awaiting_overwrite = True
        _save(session_id, state)
        return WizardOutcome(
            text=(
                f"A `SKILL.md` already exists at `{target}`. "
                "Overwrite it? Reply `yes` / `no` / `back`."
            ),
        )

    skill = SkillDef(
        name=name,
        description=d["description"],
        prompt=d["prompt"],
        argument_hint=d.get("argument_hint", ""),
        tools=d.get("tools", []),
        user_invocable=d.get("user_invocable", True),
        disable_model_invocation=d.get("disable_model_invocation", False),
        context=d.get("context", ""),
    )

    try:
        path = write_skill_def(skill, skills_dir, overwrite=overwrite)
    except Exception as e:
        logger.exception("Failed to write SKILL.md")
        _clear(session_id)
        return WizardOutcome(
            text=f"Failed to write the skill file: {e}",
            finished=True,
        )

    # Targeted load -- don't rely on ``reload_skills()`` here because the
    # wizard's destination directory may not be in ``_skills_dirs`` (e.g.
    # a fresh project where the user has only ever created via wizard).
    # Re-parse the file we just wrote so the registry reflects exactly
    # what's on disk, then register.
    try:
        loaded = parse_skill_file(path)
        if loaded is None:
            raise RuntimeError("parse_skill_file returned None")
        register_skill(loaded)
    except Exception as e:
        logger.exception("Failed to register newly created skill")
        _clear(session_id)
        return WizardOutcome(
            text=(
                f"Wrote `{path}` but failed to register the skill: {e}. "
                "Run `/reload` to retry."
            ),
            finished=True,
            skills_updated=True,
        )

    _clear(session_id)
    return WizardOutcome(
        text=(
            f"**Skill `/{name}` created.**\n\n"
            f"- File: `{path}`\n"
            f"- Invoke: `/{name}`\n\n"
            f"It's loaded -- try it now."
        ),
        finished=True,
        skills_updated=True,
    )
