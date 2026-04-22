"""Tests for ``@agent-<name>`` chat + scheduler dispatch (F2 of CC parity plan).

Covers:
  - Regex shape: valid names, plugin-namespaced, junk inputs
  - Chat dispatch: matched agent runs via ``run_agent_once``, assistant message
    appears, AgentRun row is created
  - Chat dispatch: unknown ``@agent-`` falls through (no AgentRun, no early
    return) so main Claude sees the literal text
  - Scheduler dispatch: ``@agent-foo`` prompt resolves to the agent path; the
    pre-existing ``/agent-foo`` cross-category fallback is gone (skill/command
    only on the ``/`` branch)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from handlers.agent_handler import AGENT_MENTION_RE


# ── Regex shape ─────────────────────────────────────────────────────────

class TestMentionRegex:
    def test_simple_name(self):
        m = AGENT_MENTION_RE.match("@agent-foo")
        assert m and m.group(1) == "foo" and (m.group(2) or "") == ""

    def test_with_args(self):
        m = AGENT_MENTION_RE.match("@agent-person-research find John at Acme")
        assert m and m.group(1) == "person-research"
        assert m.group(2) == "find John at Acme"

    def test_kebab_case_name(self):
        m = AGENT_MENTION_RE.match("@agent-pr-triage")
        assert m and m.group(1) == "pr-triage"

    def test_plugin_namespaced(self):
        m = AGENT_MENTION_RE.match("@agent-finance:trader buy AAPL")
        assert m and m.group(1) == "finance:trader"
        assert m.group(2) == "buy AAPL"

    def test_multiline_args(self):
        m = AGENT_MENTION_RE.match("@agent-summarizer here is\nmulti-line\ninput")
        assert m and m.group(1) == "summarizer"
        assert "multi-line" in (m.group(2) or "")

    def test_rejects_uppercase_in_name(self):
        assert AGENT_MENTION_RE.match("@agent-Foo") is None

    def test_rejects_empty_name(self):
        assert AGENT_MENTION_RE.match("@agent-") is None

    def test_rejects_no_prefix(self):
        assert AGENT_MENTION_RE.match("agent-foo do thing") is None

    def test_rejects_leading_dash(self):
        assert AGENT_MENTION_RE.match("@agent--bad") is None


# ── Chat dispatch ───────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def chat_db(test_db, test_user):
    @asynccontextmanager
    async def _get_db():
        yield test_db

    with patch("core.agent_crud.get_db", side_effect=_get_db), \
         patch("core.database.get_db", side_effect=_get_db), \
         patch("core.agent_runner.get_db", side_effect=_get_db):
        yield test_db, test_user


class TestChatDispatch:
    @pytest.mark.asyncio
    async def test_known_agent_dispatches_via_run_agent_once(self, chat_db):
        """``@agent-foo X`` in chat resolves to the AgentDefinition and runs
        through ``run_agent_once`` -- short-circuits before the main loop."""
        from core.agent_crud import create_agent
        from core.agent_runner import AgentRunResult
        from core.session import (
            create_connection, create_conv_session, destroy_connection,
            set_current_session,
        )
        from handlers.agent_handler import handle_user_message

        db, user = chat_db
        agent = await create_agent(
            user_id=user.id, name="person-research",
            description="Research a person.",
            system_prompt="You research people.",
            tools=["WebSearch"],
        )

        sid, cid = "mention-sess-1", "mention-cid-1"
        create_connection(sid, user_id=user.id)
        create_conv_session(cid, sid)
        set_current_session(sid)

        sent: list[tuple[str, dict]] = []

        async def send_event(name, payload):
            sent.append((name, payload))

        fake_run = AgentRunResult(
            run_id="run-xyz", status="completed",
            result_text="John works at Acme.", error=None, tokens_used=42,
        )
        try:
            with patch(
                "core.agent_runner.run_agent_once",
                new=AsyncMock(return_value=fake_run),
            ) as run_mock, patch(
                "handlers.agent_handler.save_conversation",
                new=AsyncMock(return_value="conv-saved"),
            ):
                await handle_user_message(
                    sid, cid,
                    {"text": "@agent-person-research find John at Acme"},
                    send_event,
                    build_system_prompt=lambda *a, **kw: "ignored",
                    outputs_dir=None,
                )

            run_mock.assert_awaited_once()
            args, kwargs = run_mock.await_args
            assert args[0].id == agent.id  # the AgentDefinition we created
            assert args[1] == "find John at Acme"
            assert kwargs.get("trigger_type") == "mention"

            event_names = [n for n, _ in sent]
            assert "skill_activated" in event_names
            assert "text_delta" in event_names
            assert "turn_complete" in event_names
            text_payloads = [p["text"] for n, p in sent if n == "text_delta"]
            assert any("John works at Acme" in t for t in text_payloads)
        finally:
            destroy_connection(sid)

    @pytest.mark.asyncio
    async def test_unknown_agent_falls_through(self, chat_db, mock_anthropic_client):
        """``@agent-nope ...`` for a non-existent name does NOT dispatch --
        message flows to the main agent loop as literal text."""
        from core.session import (
            create_connection, create_conv_session, destroy_connection,
            set_current_session,
        )
        from handlers.agent_handler import handle_user_message
        from tests.conftest import _build_text_events

        _, user = chat_db
        sid, cid = "mention-sess-2", "mention-cid-2"
        create_connection(sid, user_id=user.id)
        create_conv_session(cid, sid)
        set_current_session(sid)

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("main loop saw it")])

        sent: list[tuple[str, dict]] = []

        async def send_event(name, payload):
            sent.append((name, payload))

        try:
            with patch(
                "core.agent_runner.run_agent_once",
                new=AsyncMock(),
            ) as run_mock, patch(
                "handlers.agent_handler.save_conversation",
                new=AsyncMock(return_value="conv-x"),
            ):
                await handle_user_message(
                    sid, cid,
                    {"text": "@agent-nope hi"},
                    send_event,
                    build_system_prompt=lambda *a, **kw: "ignored",
                    outputs_dir=None,
                )

            # No agent dispatch happened
            run_mock.assert_not_awaited()
            # Main loop produced the assistant text
            text_payloads = [p["text"] for n, p in sent if n == "text_delta"]
            assert any("main loop saw it" in t for t in text_payloads)
        finally:
            destroy_connection(sid)


# ── Scheduler dispatch ──────────────────────────────────────────────────

@pytest_asyncio.fixture
async def sched_db(test_db, test_user):
    @asynccontextmanager
    async def _get_db():
        yield test_db

    with patch("core.scheduler.get_db", side_effect=_get_db), \
         patch("core.agent_crud.get_db", side_effect=_get_db), \
         patch("core.agent_runner.get_db", side_effect=_get_db):
        yield test_db, test_user


class TestSchedulerDispatch:
    @pytest.mark.asyncio
    async def test_at_agent_prompt_dispatches_to_agent(self, sched_db):
        """A schedule whose prompt is ``@agent-foo args`` runs ``foo`` via
        ``run_agent_once`` (cron trigger)."""
        from core.agent_crud import create_agent
        from core.agent_runner import AgentRunResult
        from core.scheduler import _execute_schedule

        db, user = sched_db
        agent = await create_agent(
            user_id=user.id, name="briefing",
            description="Daily briefing.",
            system_prompt="You write briefings.",
            tools=["WebSearch"],
        )

        fake_run = AgentRunResult(
            run_id="run-sched", status="completed",
            result_text="Daily briefing text.",
            error=None, tokens_used=10, conversation_id="conv-sched",
        )

        with patch(
            "core.agent_runner.run_agent_once",
            new=AsyncMock(return_value=fake_run),
        ) as run_mock:
            await _execute_schedule(
                schedule_id="sched-id-1",
                user_id=user.id,
                name="morning",
                prompt="@agent-briefing summarise overnight news",
                cron_expression="0 9 * * *",
                user_timezone="UTC",
            )

        run_mock.assert_awaited_once()
        args, kwargs = run_mock.await_args
        assert args[0].id == agent.id
        assert args[1] == "summarise overnight news"
        assert kwargs.get("trigger_type") == "cron"

    @pytest.mark.asyncio
    async def test_slash_does_not_fall_through_to_agent(self, sched_db, mock_anthropic_client):
        """Plan-mandated removal: a Schedule with ``/foo args`` must NOT
        escalate to an AgentDefinition named ``foo``. The ``/`` branch is
        skill/command only; agents require the explicit ``@agent-`` prefix."""
        from core.agent_crud import create_agent
        from core.scheduler import _execute_schedule
        from tests.conftest import _build_text_events

        db, user = sched_db
        agent = await create_agent(
            user_id=user.id, name="should-not-fire",
            description="Would have fired under the old fallback.",
            system_prompt="You should NOT have run.",
            tools=["Read"],
        )

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("generic loop ran instead")])

        with patch(
            "core.agent_runner.run_agent_once",
            new=AsyncMock(),
        ) as run_mock:
            await _execute_schedule(
                schedule_id="sched-id-2",
                user_id=user.id,
                name="legacy-form",
                prompt="/should-not-fire do the thing",
                cron_expression="0 9 * * *",
                user_timezone="UTC",
            )

        # The agent name matches a real agent, but ``/`` no longer resolves
        # to it -- run_agent_once must not have been called.
        run_mock.assert_not_awaited()
