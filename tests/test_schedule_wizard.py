"""Tests for W3: ``/schedule`` interactive wizard.

Covers:
  - Wizard state machine: kind → target → prompt → when → tz → name → review.
  - Freeform path skips the ``target`` step.
  - Agent / skill paths auto-prefix the dispatch token (``@agent-x`` / ``/y``)
    so the persisted prompt uses deterministic dispatch.
  - ``when`` step accepts both NL ("every weekday at 9am") and raw cron;
    invalid input re-asks with the parser's specific guidance.
  - Timezone validation + default fallback.
  - Duplicate-name on confirm bounces back to the name step rather than
    aborting the wizard.
  - Cancel clears state. Back rewinds; back from prompt skips the
    target step when kind=freeform.
  - Chat wiring: bare ``/schedule`` opens the wizard; subcommand path
    (``/schedule list``) does not; follow-up turns route to the wizard
    and don't pollute the conversation log.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from handlers.schedule_wizard import (
    cancel_wizard,
    handle_wizard_input,
    is_wizard_active,
    start_wizard,
)


@pytest_asyncio.fixture
async def wizard_session(test_user):
    from core.session import (
        create_connection, destroy_connection, set_current_session,
    )

    sid = "sched-wiz-sess"
    create_connection(sid, user_id=test_user.id)
    set_current_session(sid)
    try:
        yield sid, test_user.id
    finally:
        destroy_connection(sid)


@pytest_asyncio.fixture
async def wizard_db(test_db, test_user):
    """Patch get_db across the modules the wizard touches."""
    @asynccontextmanager
    async def _get_db():
        yield test_db

    with patch("core.database.get_db", side_effect=_get_db), \
         patch("core.schedule_crud.get_db", side_effect=_get_db), \
         patch("core.scheduler.get_db", side_effect=_get_db), \
         patch("core.agent_crud.get_db", side_effect=_get_db):
        yield test_db, test_user


# ── State-machine basics ─────────────────────────────────────────────────


class TestWizardBasics:
    @pytest.mark.asyncio
    async def test_start_emits_step_one(self, wizard_session):
        sid, user_id = wizard_session
        first = start_wizard(sid, user_id)
        assert "Step 1" in first
        assert "agent" in first and "skill" in first and "freeform" in first
        assert is_wizard_active(sid)

    @pytest.mark.asyncio
    async def test_cancel_clears_state(self, wizard_session):
        sid, user_id = wizard_session
        start_wizard(sid, user_id)
        out = await handle_wizard_input(sid, user_id, "cancel")
        assert out.finished is True
        assert not is_wizard_active(sid)

    @pytest.mark.asyncio
    async def test_unknown_kind_re_asks(self, wizard_session):
        sid, user_id = wizard_session
        start_wizard(sid, user_id)
        out = await handle_wizard_input(sid, user_id, "magic")
        assert out.finished is False
        assert "agent" in out.text and "skill" in out.text


# ── Freeform path ────────────────────────────────────────────────────────


class TestFreeformPath:
    @pytest.mark.asyncio
    async def test_freeform_skips_target_step(self, wizard_session):
        sid, user_id = wizard_session
        start_wizard(sid, user_id)
        out = await handle_wizard_input(sid, user_id, "freeform")
        # Should land on the prompt step, not the target picker.
        assert "Step 3" in out.text and "prompt" in out.text.lower()

    @pytest.mark.asyncio
    async def test_freeform_back_from_prompt_returns_to_kind(self, wizard_session):
        sid, user_id = wizard_session
        start_wizard(sid, user_id)
        await handle_wizard_input(sid, user_id, "freeform")
        out = await handle_wizard_input(sid, user_id, "back")
        # Should land on Step 1 (kind), skipping the target step we never
        # actually entered.
        assert "Step 1" in out.text

    @pytest.mark.asyncio
    async def test_freeform_persisted_prompt_is_verbatim(
        self, wizard_session, wizard_db,
    ):
        sid, user_id = wizard_session
        start_wizard(sid, user_id)
        await handle_wizard_input(sid, user_id, "freeform")
        await handle_wizard_input(sid, user_id, "Summarize overnight news.")
        await handle_wizard_input(sid, user_id, "every weekday at 9am")
        await handle_wizard_input(sid, user_id, "skip")  # tz default
        await handle_wizard_input(sid, user_id, "Morning brief")
        out = await handle_wizard_input(sid, user_id, "confirm")
        assert out.finished is True
        assert out.schedules_updated is True

        from core.schedule_crud import list_schedules
        rows = await list_schedules(user_id)
        assert any(
            r.prompt == "Summarize overnight news."
            and r.cron_expression == "0 9 * * 1-5"
            for r in rows
        )


# ── Agent path ──────────────────────────────────────────────────────────


class TestAgentPath:
    @pytest.mark.asyncio
    async def test_agent_prompt_is_token_prefixed(
        self, wizard_session, wizard_db,
    ):
        sid, user_id = wizard_session
        start_wizard(sid, user_id)
        await handle_wizard_input(sid, user_id, "agent")
        await handle_wizard_input(sid, user_id, "person-research")
        await handle_wizard_input(sid, user_id, "Research overnight market movers")
        await handle_wizard_input(sid, user_id, "every weekday at 9am")
        await handle_wizard_input(sid, user_id, "skip")
        await handle_wizard_input(sid, user_id, "Morning brief")
        out = await handle_wizard_input(sid, user_id, "confirm")
        assert out.finished is True

        from core.schedule_crud import list_schedules
        rows = await list_schedules(user_id)
        assert any(
            r.prompt == "@agent-person-research Research overnight market movers"
            for r in rows
        ), [r.prompt for r in rows]

    @pytest.mark.asyncio
    async def test_agent_target_strips_at_prefix(
        self, wizard_session, wizard_db,
    ):
        """User pastes ``@agent-foo`` into the target step -- wizard should
        strip the prefix rather than build ``@agent-@agent-foo``."""
        sid, user_id = wizard_session
        start_wizard(sid, user_id)
        await handle_wizard_input(sid, user_id, "agent")
        await handle_wizard_input(sid, user_id, "@agent-trader")
        out = await handle_wizard_input(sid, user_id, "")
        # Empty body re-asks (prompt is required for agent path).
        assert out.finished is False
        await handle_wizard_input(sid, user_id, "Run market analysis")
        await handle_wizard_input(sid, user_id, "every weekday at 9am")
        await handle_wizard_input(sid, user_id, "skip")
        await handle_wizard_input(sid, user_id, "Trader run")
        await handle_wizard_input(sid, user_id, "confirm")

        from core.schedule_crud import list_schedules
        rows = await list_schedules(user_id)
        assert any(r.prompt.startswith("@agent-trader ") for r in rows)
        assert not any("@agent-@agent-" in r.prompt for r in rows)


# ── Skill path ──────────────────────────────────────────────────────────


class TestSkillPath:
    @pytest.mark.asyncio
    async def test_skill_prompt_token_prefixed(self, wizard_session, wizard_db):
        sid, user_id = wizard_session
        start_wizard(sid, user_id)
        await handle_wizard_input(sid, user_id, "skill")
        await handle_wizard_input(sid, user_id, "/digest")
        await handle_wizard_input(sid, user_id, "weekly")
        await handle_wizard_input(sid, user_id, "every Monday at 8am")
        await handle_wizard_input(sid, user_id, "skip")
        await handle_wizard_input(sid, user_id, "Weekly digest")
        out = await handle_wizard_input(sid, user_id, "confirm")
        assert out.finished is True

        from core.schedule_crud import list_schedules
        rows = await list_schedules(user_id)
        assert any(r.prompt == "/digest weekly" for r in rows)

    @pytest.mark.asyncio
    async def test_skill_skip_args_invokes_bare_command(
        self, wizard_session, wizard_db,
    ):
        sid, user_id = wizard_session
        start_wizard(sid, user_id)
        await handle_wizard_input(sid, user_id, "skill")
        await handle_wizard_input(sid, user_id, "ping")
        await handle_wizard_input(sid, user_id, "skip")
        await handle_wizard_input(sid, user_id, "every 30 minutes")
        await handle_wizard_input(sid, user_id, "skip")
        await handle_wizard_input(sid, user_id, "Pinger")
        await handle_wizard_input(sid, user_id, "confirm")

        from core.schedule_crud import list_schedules
        rows = await list_schedules(user_id)
        assert any(r.prompt == "/ping" for r in rows)


# ── When step (NL + cron) ───────────────────────────────────────────────


class TestWhenStep:
    @pytest.mark.asyncio
    async def test_invalid_when_re_asks_with_specific_guidance(
        self, wizard_session,
    ):
        sid, user_id = wizard_session
        start_wizard(sid, user_id)
        await handle_wizard_input(sid, user_id, "freeform")
        await handle_wizard_input(sid, user_id, "do thing")
        out = await handle_wizard_input(sid, user_id, "maybe Tuesdays?")
        assert out.finished is False
        # Specific guidance, not a generic "no".
        assert "every weekday at 9am" in out.text or "5-field cron" in out.text

    @pytest.mark.asyncio
    async def test_raw_cron_passthrough(self, wizard_session, wizard_db):
        sid, user_id = wizard_session
        start_wizard(sid, user_id)
        await handle_wizard_input(sid, user_id, "freeform")
        await handle_wizard_input(sid, user_id, "task")
        await handle_wizard_input(sid, user_id, "0 */6 * * *")
        await handle_wizard_input(sid, user_id, "skip")
        await handle_wizard_input(sid, user_id, "Six-hourly")
        await handle_wizard_input(sid, user_id, "confirm")

        from core.schedule_crud import list_schedules
        rows = await list_schedules(user_id)
        assert any(r.cron_expression == "0 */6 * * *" for r in rows)


# ── Timezone step ───────────────────────────────────────────────────────


class TestTimezone:
    @pytest.mark.asyncio
    async def test_timezone_default_is_europe_london(
        self, wizard_session, wizard_db,
    ):
        sid, user_id = wizard_session
        start_wizard(sid, user_id)
        await handle_wizard_input(sid, user_id, "freeform")
        await handle_wizard_input(sid, user_id, "task")
        await handle_wizard_input(sid, user_id, "daily at 9am")
        await handle_wizard_input(sid, user_id, "skip")
        await handle_wizard_input(sid, user_id, "Daily")
        await handle_wizard_input(sid, user_id, "confirm")

        from core.schedule_crud import list_schedules
        rows = await list_schedules(user_id)
        assert any(r.user_timezone == "Europe/London" for r in rows)

    @pytest.mark.asyncio
    async def test_timezone_override_validated(self, wizard_session):
        sid, user_id = wizard_session
        start_wizard(sid, user_id)
        await handle_wizard_input(sid, user_id, "freeform")
        await handle_wizard_input(sid, user_id, "task")
        await handle_wizard_input(sid, user_id, "daily at 9am")
        out = await handle_wizard_input(sid, user_id, "Mars/Olympus")
        assert "isn't a recognised IANA timezone" in out.text
        # Wizard should not have advanced.
        assert is_wizard_active(sid)


# ── Name conflict ───────────────────────────────────────────────────────


class TestNameConflict:
    @pytest.mark.asyncio
    async def test_duplicate_name_bounces_back_to_name_step(
        self, wizard_session, wizard_db,
    ):
        sid, user_id = wizard_session
        # Create a schedule with the name we'll try to reuse.
        from core.schedule_crud import create_schedule
        await create_schedule(user_id, "Brief", "0 9 * * *", "old", "UTC")

        start_wizard(sid, user_id)
        await handle_wizard_input(sid, user_id, "freeform")
        await handle_wizard_input(sid, user_id, "new prompt")
        await handle_wizard_input(sid, user_id, "daily at 10am")
        await handle_wizard_input(sid, user_id, "skip")
        await handle_wizard_input(sid, user_id, "Brief")
        first = await handle_wizard_input(sid, user_id, "confirm")
        assert "already exists" in first.text

        # Reply 'yes' -> wizard goes back to the name step.
        retry = await handle_wizard_input(sid, user_id, "yes")
        assert "Step 6" in retry.text or "name" in retry.text.lower()

        # Use a fresh name and confirm again.
        await handle_wizard_input(sid, user_id, "Brief 2")
        out = await handle_wizard_input(sid, user_id, "confirm")
        assert out.finished is True
        assert out.schedules_updated is True


# ── Chat wiring ─────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def chat_wizard(test_db, test_user):
    from core.session import (
        create_connection, create_conv_session, destroy_connection,
        set_current_session,
    )

    sid, cid = "chat-sched-wiz", "chat-sched-cid"
    create_connection(sid, user_id=test_user.id)
    create_conv_session(cid, sid)
    set_current_session(sid)

    @asynccontextmanager
    async def _get_db():
        yield test_db

    with patch("core.database.get_db", side_effect=_get_db), \
         patch("core.schedule_crud.get_db", side_effect=_get_db), \
         patch("core.scheduler.get_db", side_effect=_get_db), \
         patch("core.agent_crud.get_db", side_effect=_get_db):
        try:
            yield sid, cid, test_user
        finally:
            destroy_connection(sid)


class TestChatWiring:
    @pytest.mark.asyncio
    async def test_bare_slash_schedule_starts_wizard(self, chat_wizard):
        from handlers.agent_handler import handle_user_message

        sid, cid, user = chat_wizard
        sent: list[tuple[str, dict]] = []

        async def send_event(name, payload):
            sent.append((name, payload))

        with patch(
            "handlers.agent_handler.save_conversation",
            new=AsyncMock(return_value="conv-x"),
        ):
            await handle_user_message(
                sid, cid,
                {"text": "/schedule"},
                send_event,
                build_system_prompt=lambda *a, **kw: "ignored",
                outputs_dir=None,
            )

        assert is_wizard_active(sid)
        text_payload = "".join(p["text"] for n, p in sent if n == "text_delta")
        assert "Step 1" in text_payload

    @pytest.mark.asyncio
    async def test_slash_schedule_subcommand_does_not_start_wizard(
        self, chat_wizard,
    ):
        """``/schedule list`` keeps the existing one-liner path -- only bare
        ``/schedule`` opens the wizard."""
        from handlers.agent_handler import handle_user_message

        sid, cid, user = chat_wizard
        sent: list[tuple[str, dict]] = []

        async def send_event(name, payload):
            sent.append((name, payload))

        with patch(
            "handlers.agent_handler.save_conversation",
            new=AsyncMock(return_value="conv-x"),
        ):
            await handle_user_message(
                sid, cid,
                {"text": "/schedule list"},
                send_event,
                build_system_prompt=lambda *a, **kw: "ignored",
                outputs_dir=None,
            )

        assert not is_wizard_active(sid)

    @pytest.mark.asyncio
    async def test_followup_routes_to_wizard_and_skips_persistence(
        self, chat_wizard,
    ):
        from handlers.agent_handler import handle_user_message

        sid, cid, user = chat_wizard
        start_wizard(sid, user.id)

        sent: list[tuple[str, dict]] = []

        async def send_event(name, payload):
            sent.append((name, payload))

        with patch(
            "handlers.agent_handler.save_conversation",
            new=AsyncMock(return_value="conv-x"),
        ):
            await handle_user_message(
                sid, cid,
                {"text": "freeform"},
                send_event,
                build_system_prompt=lambda *a, **kw: "ignored",
                outputs_dir=None,
            )

        text_payload = "".join(p["text"] for n, p in sent if n == "text_delta")
        assert "Step 3" in text_payload
        # Wizard turn must NOT pollute the conversation log.
        from core.session import conv_session_get
        msgs = conv_session_get(cid, "messages") or []
        assert all(m.get("content") != "freeform" for m in msgs)

    @pytest.mark.asyncio
    async def test_cancel_in_chat_clears_wizard(self, chat_wizard):
        from handlers.agent_handler import handle_user_message

        sid, cid, user = chat_wizard
        start_wizard(sid, user.id)

        sent: list[tuple[str, dict]] = []

        async def send_event(name, payload):
            sent.append((name, payload))

        with patch(
            "handlers.agent_handler.save_conversation",
            new=AsyncMock(return_value="conv-x"),
        ):
            await handle_user_message(
                sid, cid,
                {"text": "cancel"},
                send_event,
                build_system_prompt=lambda *a, **kw: "ignored",
                outputs_dir=None,
            )

        assert not is_wizard_active(sid)
