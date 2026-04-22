"""Conversational wizard for ``/schedule`` (W3 of CC parity plan).

Same pattern as ``agents_wizard`` and ``skills_wizard``: a per-session
state machine intercepted at the top of ``handle_user_message`` so each
follow-up reply belongs to the wizard, bypassing slash routing and
conversation persistence. On confirm it builds the same arguments the
existing ``handle_schedule_command("create ...")`` path expects and calls
``create_schedule`` directly -- scheduling logic isn't duplicated.

Flow:
  1. **kind** -- agent / skill / freeform.
  2. **target** -- pick name (when kind=agent or kind=skill); skipped for freeform.
  3. **prompt** -- the argument body (or freeform prompt). For agent/skill picks
     the wizard automatically prefixes the explicit dispatch token (`@agent-<name>`
     or `/<skill>`) so scheduled runs use deterministic dispatch, never NL.
  4. **when** -- natural language (or raw cron). Echoed back via ``nl_cron.parse``
     so the user can catch interpretation drift before persistence.
  5. **timezone** -- defaults to user profile / Europe/London; one-shot override.
  6. **name** -- short label for the schedule row.
  7. **review** -> **confirm**.

Cancellation, ``back`` rewind, and overwrite-on-name-conflict are handled
the same way as the agent/skill wizards.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from core.session import connection_get, connection_set

logger = logging.getLogger(__name__)


_DEFAULT_TIMEZONE = "Europe/London"


def _is_valid_tz(tz: str) -> bool:
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz)
        return True
    except Exception:
        return False


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
    ask: Callable[["ScheduleWizardState"], str]
    consume: Callable[["ScheduleWizardState", str], int | str]
    label: str = ""


@dataclass
class ScheduleWizardState:
    user_id: str
    step_index: int = 0
    data: dict[str, Any] = field(default_factory=dict)
    awaiting_overwrite: bool = False


# ── Steps ───────────────────────────────────────────────────────────────


def _next(s: ScheduleWizardState) -> int:
    return s.step_index + 1


def _step_kind() -> _Step:
    def ask(s: ScheduleWizardState) -> str:
        return (
            "**Step 1 / 6 - what should this schedule run?**\n\n"
            "Pick one:\n"
            "- `agent` -- one of your custom agents (dispatched via `@agent-<name>`)\n"
            "- `skill` -- one of your skills (dispatched via `/<name>`)\n"
            "- `freeform` -- a plain prompt run by the general-purpose agent"
        )

    def consume(s: ScheduleWizardState, text: str) -> int | str:
        choice = text.strip().lower()
        if choice in ("a", "agent", "agents"):
            s.data["kind"] = "agent"
            return _next(s)
        if choice in ("s", "skill", "skills"):
            s.data["kind"] = "skill"
            return _next(s)
        if choice in ("f", "free", "freeform", "free-form", "prompt"):
            s.data["kind"] = "freeform"
            # Skip the picker step (target).
            return s.step_index + 2
        return "Reply `agent`, `skill`, or `freeform`."

    return _Step("kind", ask, consume, label="Kind")


def _step_target() -> _Step:
    """Pick which agent or skill to dispatch. Skipped for freeform.

    Lookup is deferred until ``ask`` runs so tests don't have to construct a
    DB / skills registry up front when exercising other steps.
    """
    def ask(s: ScheduleWizardState) -> str:
        kind = s.data.get("kind")
        if kind == "agent":
            names = _list_agent_names_sync(s.user_id)
            if not names:
                return (
                    "**Step 2 / 6 - target**\n\n"
                    "You have no enabled agents yet. Type the agent name "
                    "anyway (it'll be created later), or `back` to switch "
                    "to a skill or freeform schedule."
                )
            roster = ", ".join(f"`{n}`" for n in names[:10])
            more = "" if len(names) <= 10 else f" (and {len(names) - 10} more)"
            return (
                "**Step 2 / 6 - which agent?**\n\n"
                f"Available: {roster}{more}.\n\n"
                "Type the agent name."
            )
        if kind == "skill":
            names = _list_skill_names_sync()
            roster = ", ".join(f"`/{n}`" for n in names[:10])
            more = "" if len(names) <= 10 else f" (and {len(names) - 10} more)"
            return (
                "**Step 2 / 6 - which skill?**\n\n"
                f"Available: {roster}{more}.\n\n"
                "Type the skill name (without the leading `/`)."
            )
        # Freeform should never land here -- guarded by step_kind advancing +2.
        return "Skipping target step (freeform schedule)."

    def consume(s: ScheduleWizardState, text: str) -> int | str:
        name = text.strip().lstrip("/").lstrip("@")
        # ``@agent-foo`` -> ``foo``
        if name.startswith("agent-"):
            name = name[len("agent-"):]
        if not name:
            return "Please type a name."
        s.data["target"] = name
        return _next(s)

    return _Step("target", ask, consume, label="Target")


def _step_prompt() -> _Step:
    def ask(s: ScheduleWizardState) -> str:
        kind = s.data["kind"]
        if kind == "agent":
            return (
                "**Step 3 / 6 - prompt body**\n\n"
                f"What should `@agent-{s.data['target']}` be told each run? "
                "Type the prompt body -- the wizard prefixes `@agent-<name> ` "
                "for you, so deterministic dispatch is guaranteed."
            )
        if kind == "skill":
            return (
                "**Step 3 / 6 - prompt arguments**\n\n"
                f"Arguments to pass to `/{s.data['target']}` each run, "
                "or `skip` to invoke it with no arguments."
            )
        return (
            "**Step 3 / 6 - prompt**\n\n"
            "The full prompt to send each run. Use plain language; this "
            "runs against the general-purpose agent with full tool access."
        )

    def consume(s: ScheduleWizardState, text: str) -> int | str:
        kind = s.data["kind"]
        body = text.strip()
        if kind == "freeform":
            if not body:
                return "A freeform schedule needs a prompt body."
            s.data["prompt"] = body
            return _next(s)
        if kind == "agent":
            if not body:
                return "An agent schedule needs a prompt body."
            s.data["prompt"] = f"@agent-{s.data['target']} {body}".strip()
            return _next(s)
        # skill
        if _optional_skip(body):
            s.data["prompt"] = f"/{s.data['target']}"
        else:
            s.data["prompt"] = f"/{s.data['target']} {body}".strip()
        return _next(s)

    return _Step("prompt", ask, consume, label="Prompt")


def _step_when() -> _Step:
    def ask(s: ScheduleWizardState) -> str:
        return (
            "**Step 4 / 6 - when?**\n\n"
            "Describe the schedule in plain English. Examples:\n"
            "- `every weekday at 9am`\n"
            "- `every Monday at 8:30`\n"
            "- `daily at 18:30`\n"
            "- `every 2 hours`\n"
            "- `first of every month at 9am`\n\n"
            "Or paste a 5-field cron expression like `0 9 * * 1-5`."
        )

    def consume(s: ScheduleWizardState, text: str) -> int | str:
        from core.nl_cron import parse, NlCronParseError
        try:
            r = parse(text)
        except NlCronParseError as e:
            return str(e)
        s.data["cron"] = r.cron
        s.data["when_human"] = r.human
        return _next(s)

    return _Step("when", ask, consume, label="When")


def _step_timezone() -> _Step:
    def ask(s: ScheduleWizardState) -> str:
        return (
            "**Step 5 / 6 - timezone** _(optional)_\n\n"
            f"Default: `{_DEFAULT_TIMEZONE}`. Type an IANA timezone like "
            "`US/Eastern` or `UTC`, or `skip` to keep the default. "
            "(This is the zone the cron times will be interpreted in.)"
        )

    def consume(s: ScheduleWizardState, text: str) -> int | str:
        if _optional_skip(text):
            s.data["timezone"] = _DEFAULT_TIMEZONE
            return _next(s)
        tz = text.strip()
        if not _is_valid_tz(tz):
            return (
                f"`{tz}` isn't a recognised IANA timezone. Try `Europe/London`, "
                "`US/Eastern`, `UTC`, etc."
            )
        s.data["timezone"] = tz
        return _next(s)

    return _Step("timezone", ask, consume, label="Timezone")


def _step_name() -> _Step:
    def ask(s: ScheduleWizardState) -> str:
        # Suggest a default based on the target / prompt.
        if s.data.get("target"):
            default = s.data["target"].replace("-", " ").title()
        else:
            words = s.data.get("prompt", "").split()
            default = " ".join(words[:4]) or "Scheduled task"
        s.data.setdefault("_default_name", default)
        return (
            "**Step 6 / 6 - name**\n\n"
            f"A short label for this schedule. Default: `{default}`. "
            "Type a name, or `skip` to use the default."
        )

    def consume(s: ScheduleWizardState, text: str) -> int | str:
        if _optional_skip(text):
            s.data["name"] = s.data.get("_default_name") or "Scheduled task"
        else:
            s.data["name"] = text.strip()
        return _next(s)

    return _Step("name", ask, consume, label="Name")


def _step_review() -> _Step:
    def ask(s: ScheduleWizardState) -> str:
        d = s.data
        lines = [
            "**Review** -- please confirm:\n",
            f"- **Kind:** {d['kind']}",
        ]
        if d.get("target"):
            lines.append(f"- **Target:** `{d['target']}`")
        prompt_preview = d["prompt"]
        if len(prompt_preview) > 200:
            prompt_preview = prompt_preview[:200] + "..."
        lines += [
            f"- **Prompt:** {prompt_preview}",
            f"- **When:** {d['when_human']} (cron `{d['cron']}`)",
            f"- **Timezone:** {d['timezone']}",
            f"- **Name:** {d['name']}",
            "",
            "Reply `confirm` to create the schedule, `back` to revise the "
            "previous step, or `cancel` to abort.",
        ]
        return "\n".join(lines)

    def consume(s: ScheduleWizardState, text: str) -> int | str:
        return "Reply `confirm`, `back`, or `cancel`."

    return _Step("review", ask, consume, label="Review")


def _build_steps() -> list[_Step]:
    return [
        _step_kind(),
        _step_target(),
        _step_prompt(),
        _step_when(),
        _step_timezone(),
        _step_name(),
        _step_review(),
    ]


# ── Lookups (sync wrappers used inside step ``ask`` functions) ──────────


def _list_agent_names_sync(user_id: str) -> list[str]:
    """Best-effort sync helper for the picker. Empty list on any failure --
    the wizard remains usable; the user can type the name freehand.
    """
    import asyncio
    try:
        from core.agent_crud import list_agents
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            agents = asyncio.run(list_agents(user_id))
        else:
            # We're inside a running loop -- use a thread to avoid nesting.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(asyncio.run, list_agents(user_id))
                agents = fut.result(timeout=5)
        return [a.name for a in agents if a.enabled]
    except Exception as e:
        logger.debug(f"_list_agent_names_sync failed: {e}")
        return []


def _list_skill_names_sync() -> list[str]:
    try:
        from skills.registry import list_skills
        return [s.name for s in list_skills() if s.user_invocable]
    except Exception as e:
        logger.debug(f"_list_skill_names_sync failed: {e}")
        return []


# ── Public API ──────────────────────────────────────────────────────────


_WIZARD_KEY = "schedule_wizard"


@dataclass
class WizardOutcome:
    text: str
    finished: bool = False
    schedules_updated: bool = False


def is_wizard_active(session_id: str) -> bool:
    return connection_get(session_id, _WIZARD_KEY) is not None


def _save(session_id: str, state: ScheduleWizardState) -> None:
    connection_set(session_id, _WIZARD_KEY, state)


def _clear(session_id: str) -> None:
    connection_set(session_id, _WIZARD_KEY, None)


def start_wizard(session_id: str, user_id: str) -> str:
    """Initialise the wizard for this session and return the first prompt."""
    state = ScheduleWizardState(user_id=user_id)
    _save(session_id, state)
    steps = _build_steps()
    return (
        "Starting `/schedule`. Type `cancel` at any time to abort, "
        "`back` to revise the previous step.\n\n"
        + steps[0].ask(state)
    )


def cancel_wizard(session_id: str) -> str:
    _clear(session_id)
    return "Cancelled. No schedule was created."


async def handle_wizard_input(
    session_id: str,
    user_id: str,
    text: str,
) -> WizardOutcome:
    """Advance the wizard one step using ``text`` as the user's reply."""
    state: ScheduleWizardState | None = connection_get(session_id, _WIZARD_KEY)
    if state is None:
        return WizardOutcome(text="No schedule wizard is in progress.", finished=True)

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
        # If we previously skipped the target step (kind=freeform), step
        # back two slots so ``back`` from "prompt" lands on "kind" rather
        # than the unreachable target step.
        prev_idx = state.step_index - 1
        if prev_idx == 1 and state.data.get("kind") == "freeform":
            prev_idx = 0
        state.step_index = prev_idx
        _save(session_id, state)
        return WizardOutcome(text=steps[state.step_index].ask(state))

    if state.awaiting_overwrite:
        b = _parse_bool(raw)
        if b is None:
            return WizardOutcome(text="Reply `yes` to use a different name, `no` to cancel, or `back`.")
        if not b:
            return WizardOutcome(text=cancel_wizard(session_id), finished=True)
        # Bounce back to the name step so the user picks a new label.
        state.awaiting_overwrite = False
        # Find the name step index dynamically.
        name_idx = next(i for i, st in enumerate(steps) if st.key == "name")
        state.step_index = name_idx
        state.data.pop("name", None)
        _save(session_id, state)
        return WizardOutcome(text=steps[name_idx].ask(state))

    if current.key == "review":
        if lower == "confirm":
            return await _finalize(session_id, state)
        return WizardOutcome(text="Reply `confirm`, `back`, or `cancel`.")

    result = current.consume(state, raw)
    if isinstance(result, str):
        return WizardOutcome(text=result)
    state.step_index = result
    _save(session_id, state)
    return WizardOutcome(text=steps[state.step_index].ask(state))


async def _finalize(
    session_id: str,
    state: ScheduleWizardState,
) -> WizardOutcome:
    """Create the Schedule row via the existing CRUD path."""
    from core.schedule_crud import create_schedule

    d = state.data
    try:
        sched = await create_schedule(
            user_id=state.user_id,
            name=d["name"],
            cron_expression=d["cron"],
            prompt=d["prompt"],
            user_timezone=d["timezone"],
        )
    except ValueError as e:
        # Most common case: duplicate name. Bounce back to the name step
        # rather than aborting -- the user can pick a different label.
        msg = str(e)
        if "already exists" in msg.lower():
            state.awaiting_overwrite = True
            _save(session_id, state)
            return WizardOutcome(
                text=(
                    f"A schedule named `{d['name']}` already exists. "
                    "Pick a different name? Reply `yes` to rename, `no` "
                    "to cancel."
                ),
            )
        _clear(session_id)
        return WizardOutcome(
            text=f"Failed to create schedule: {e}",
            finished=True,
        )
    except Exception as e:
        logger.exception("Failed to create schedule")
        _clear(session_id)
        return WizardOutcome(
            text=f"Failed to create schedule: {e}",
            finished=True,
        )

    next_run = (
        sched.next_run_at.strftime("%Y-%m-%d %H:%M UTC")
        if sched.next_run_at else "unknown"
    )
    _clear(session_id)
    return WizardOutcome(
        text=(
            f"**Schedule `{sched.name}` created.**\n\n"
            f"- ID: `{sched.id[:8]}`\n"
            f"- When: {d['when_human']} ({sched.user_timezone})\n"
            f"- Cron: `{sched.cron_expression}`\n"
            f"- Prompt: {sched.prompt}\n"
            f"- Next run: {next_run}\n\n"
            f"Use `/schedule list` to see all schedules, "
            f"`/schedule run {sched.name}` to trigger immediately."
        ),
        finished=True,
        schedules_updated=True,
    )
