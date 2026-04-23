"""CC-compatibility checks for the Agent tool and subagent resolution.

These tests pin the Claude Code drop-in contract documented in
``docs/plugin-spec.md``. If any test here fails, a CC-authored plugin
has likely stopped working -- read the Compatibility section in the
plugin spec before changing behaviour to satisfy the test.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import _build_text_events


@pytest.fixture(autouse=True)
def _patch_session():
    with (
        patch("core.agent.current_session_get", return_value="default"),
        patch("core.agent.record_usage", new_callable=AsyncMock),
    ):
        yield


@pytest.fixture
def _clean_subagent_state():
    from tools.subagent import _active_agents, _notify_callbacks
    _active_agents.clear()
    _notify_callbacks.clear()
    yield
    _active_agents.clear()
    _notify_callbacks.clear()


# ── Agent tool schema: description is optional ─────────────────────────

class TestOptionalDescription:
    def test_schema_does_not_require_description(self):
        """CC's Agent tool makes `description` optional; Light CC matches."""
        from tools.registry import _TOOLS

        entry = _TOOLS.get("Agent")
        assert entry is not None
        _handler, schema = entry
        required = schema.get("input_schema", {}).get("required", [])
        assert "subagent_type" in required
        assert "prompt" in required
        assert "description" not in required, (
            "description must be optional to match Claude Code's Agent tool"
        )

    @pytest.mark.asyncio
    async def test_handle_task_without_description(
        self, mock_anthropic_client, _clean_subagent_state,
    ):
        """A CC-style call that omits `description` should still succeed."""
        from tools.subagent import handle_task

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("ok")])

        result_json = await handle_task({
            "prompt": "Do a thing",
            "subagent_type": "default",
        })
        result = json.loads(result_json)
        assert "error" not in result, result
        assert result.get("agent_id")


# ── Builtin aliases: Explore/Plan/general-purpose ──────────────────────

class TestBuiltinAliases:
    def test_explore_alias(self):
        from core.agent_types import get_agent_type

        resolved = get_agent_type("Explore")
        assert resolved is not None
        assert resolved.name == "explorer"

    def test_plan_alias(self):
        from core.agent_types import get_agent_type

        resolved = get_agent_type("Plan")
        assert resolved is not None
        assert resolved.name == "planner"

    def test_general_purpose_alias(self):
        from core.agent_types import get_agent_type

        resolved = get_agent_type("general-purpose")
        assert resolved is not None
        assert resolved.name == "default"

    def test_aliases_are_case_insensitive(self):
        """`EXPLORE`, `explore`, `Explore` all resolve to the same builtin."""
        from core.agent_types import get_agent_type

        for spelling in ("EXPLORE", "explore", "Explore", "ExPloRe"):
            resolved = get_agent_type(spelling)
            assert resolved is not None, spelling
            assert resolved.name == "explorer"

    def test_canonical_names_still_work(self):
        """Existing lowercase names (explorer/planner/default) must win."""
        from core.agent_types import get_agent_type

        for canonical in ("explorer", "planner", "default"):
            resolved = get_agent_type(canonical)
            assert resolved is not None
            assert resolved.name == canonical

    def test_unknown_alias_returns_none(self):
        from core.agent_types import get_agent_type

        assert get_agent_type("explor") is None  # typo
        assert get_agent_type("general") is None  # partial alias
        assert get_agent_type("totally-made-up") is None

    @pytest.mark.asyncio
    async def test_user_agent_shadows_alias(
        self, mock_anthropic_client, _clean_subagent_state,
    ):
        """A user agent named after a canonical builtin takes precedence
        over both the canonical name and a CC alias that resolves to it."""
        from tools.subagent import handle_task

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("from user agent")])

        # Simulate a resolved user AgentDefinition for "explorer" by
        # short-circuiting the DB lookup. If shadowing works, the Agent
        # tool should pick this path even when the caller spells the
        # name with CC's alias ("Explore").
        class _FakeAgentDef:
            id = "user-agent-id"
            name = "explorer"
            enabled = True

        fake_def = _FakeAgentDef()

        async def _fake_resolve(name, user_id):
            if name.lower() == "explore" or name.lower() == "explorer":
                return fake_def
            return None

        async def _fake_run(agent_def, prompt, parent_session_id, *, prior_messages=None):
            # Don't touch the DB; just confirm we got the user agent.
            from tools.subagent import _RunTelemetry
            return ("from user agent", "fake-run", [], _RunTelemetry())

        with (
            patch("tools.subagent._resolve_agent_definition", side_effect=_fake_resolve),
            patch("tools.subagent._run_via_definition", side_effect=_fake_run),
        ):
            # Caller uses the CC alias spelling.
            result_json = await handle_task({
                "prompt": "search",
                "subagent_type": "Explore",
            })

        result = json.loads(result_json)
        assert "error" not in result, result
        assert result.get("result") == "from user agent"
        assert result.get("run_id") == "fake-run", (
            "run_id is only set on the definition path -- if it's missing, "
            "the builtin shadowed the user agent instead of the other way round"
        )


# ── SubagentStart hook fires on dispatch ───────────────────────────────

class TestSubagentStartHook:
    @pytest.mark.asyncio
    async def test_subagent_start_hook_fires(
        self, mock_anthropic_client, _clean_subagent_state,
    ):
        """When a SubagentStart hook is registered, handle_task must fire it
        exactly once with {subagent_type, agent_id, parent_session_id,
        description} before the subagent loop begins."""
        from tools.subagent import handle_task

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("done")])

        fire_mock = AsyncMock(return_value=[])
        with (
            patch("core.hooks.has_hooks", return_value=True),
            patch("core.hooks.fire_hooks", fire_mock),
        ):
            result_json = await handle_task({
                "prompt": "Do a thing",
                "subagent_type": "default",
                "description": "test hook",
            })

        result = json.loads(result_json)
        agent_id = result.get("agent_id")
        assert agent_id, result

        # Exactly one SubagentStart call with the documented payload.
        start_calls = [
            c for c in fire_mock.await_args_list
            if c.args and c.args[0] == "SubagentStart"
        ]
        assert len(start_calls) == 1, fire_mock.await_args_list
        payload = start_calls[0].args[1]
        assert payload["subagent_type"] == "default"
        assert payload["agent_id"] == agent_id
        assert payload["description"] == "test hook"
        assert "parent_session_id" in payload

    @pytest.mark.asyncio
    async def test_subagent_start_skipped_when_no_hooks(
        self, mock_anthropic_client, _clean_subagent_state,
    ):
        """With no SubagentStart hook registered, fire_hooks must not be
        called for that event -- the fast path stays fast."""
        from tools.subagent import handle_task

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("done")])

        fire_mock = AsyncMock(return_value=[])
        with (
            patch("core.hooks.has_hooks", return_value=False),
            patch("core.hooks.fire_hooks", fire_mock),
        ):
            await handle_task({
                "prompt": "Do a thing",
                "subagent_type": "default",
            })

        start_calls = [
            c for c in fire_mock.await_args_list
            if c.args and c.args[0] == "SubagentStart"
        ]
        assert start_calls == []


# ── Enriched result payload ────────────────────────────────────────────

class TestEnrichedPayload:
    @pytest.mark.asyncio
    async def test_foreground_payload_has_telemetry_fields(
        self, mock_anthropic_client, _clean_subagent_state,
    ):
        from tools.subagent import handle_task

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("ok")])

        result_json = await handle_task({
            "prompt": "x",
            "subagent_type": "default",
        })
        result = json.loads(result_json)
        # Existing fields still present.
        assert "result" in result
        assert "agent_id" in result
        assert "subagent_type" in result
        # Telemetry additions.
        assert result.get("status") in ("completed", "failed")
        assert isinstance(result.get("total_duration_ms"), int)
        assert isinstance(result.get("total_tool_use_count"), int)
        assert "total_tokens" in result
        usage = result.get("usage")
        assert isinstance(usage, dict)
        assert "input_tokens" in usage
        assert "output_tokens" in usage

    @pytest.mark.asyncio
    async def test_background_payload_status_started(
        self, mock_anthropic_client, _clean_subagent_state,
    ):
        from tools.subagent import handle_task

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("bg")])

        result_json = await handle_task({
            "prompt": "x",
            "subagent_type": "default",
            "run_in_background": True,
        })
        result = json.loads(result_json)
        assert result["status"] == "started"
        assert result.get("agent_id")
