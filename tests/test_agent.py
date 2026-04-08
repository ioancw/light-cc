"""Tests for the agentic tool-use loop (core/agent.py)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import (
    MockStream,
    _build_text_events,
    _build_tool_events,
    MockFinalMessage,
    MockUsage,
)


@pytest.fixture(autouse=True)
def _patch_session_and_usage():
    """Patch session lookups and usage recording so agent.run() doesn't need real state."""
    with (
        patch("core.agent.current_session_get", return_value="default"),
        patch("core.agent.record_usage", new_callable=AsyncMock),
    ):
        yield


# ── Text-only responses ─────────────────────────────────────────────────

class TestTextResponse:
    @pytest.mark.asyncio
    async def test_single_text_response(self, mock_anthropic_client):
        from core.agent import run

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("Hello, world!")])

        collected: list[str] = []
        messages = [{"role": "user", "content": "Hi"}]

        result = await run(
            messages=messages,
            tools=[],
            system="You are helpful.",
            on_text=AsyncMock(side_effect=lambda t: collected.append(t)),
            on_tool_start=AsyncMock(),
            on_tool_end=AsyncMock(),
        )

        assert len(result) == 2  # user + assistant
        assert result[-1]["role"] == "assistant"
        assert result[-1]["content"][0]["type"] == "text"
        assert "Hello, world!" in collected

    @pytest.mark.asyncio
    async def test_empty_text_response(self, mock_anthropic_client):
        from core.agent import run

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("")])

        result = await run(
            messages=[{"role": "user", "content": "Hi"}],
            tools=[],
            system="test",
            on_text=AsyncMock(),
            on_tool_start=AsyncMock(),
            on_tool_end=AsyncMock(),
        )

        assert len(result) == 2
        assert result[-1]["content"][0]["text"] == ""


# ── Tool execution ──────────────────────────────────────────────────────

class TestToolExecution:
    @pytest.mark.asyncio
    async def test_single_tool_call(self, mock_anthropic_client):
        from core.agent import run

        _, set_responses = mock_anthropic_client

        # Turn 1: Claude requests a tool call
        tool_events = _build_tool_events("tool_1", "Read", {"file_path": "/test.txt"}, index=0)
        # Turn 2: Claude responds with text after getting tool result
        text_events = _build_text_events("File contents: hello")

        set_responses([tool_events, text_events])

        tool_starts: list[tuple] = []
        tool_ends: list[tuple] = []

        async def mock_tool_start(name, inp):
            tool_starts.append((name, inp))
            return "ctx_1"

        async def mock_tool_end(ctx, result):
            tool_ends.append((ctx, result))

        with patch("core.agent.execute_tool", new_callable=AsyncMock, return_value='{"content": "hello"}'):
            with patch("core.rate_limit.check_rate_limit", return_value=(True, "")):
                result = await run(
                    messages=[{"role": "user", "content": "Read test.txt"}],
                    tools=[{"name": "Read", "description": "Read a file", "input_schema": {}}],
                    system="test",
                    on_text=AsyncMock(),
                    on_tool_start=mock_tool_start,
                    on_tool_end=mock_tool_end,
                )

        assert len(tool_starts) == 1
        assert tool_starts[0][0] == "Read"
        assert len(tool_ends) == 1
        # Messages: user, assistant (tool_use), user (tool_result), assistant (text)
        assert len(result) == 4

    @pytest.mark.asyncio
    async def test_permission_denied(self, mock_anthropic_client):
        """When on_permission_check returns a string, tool should be denied."""
        from core.agent import run

        _, set_responses = mock_anthropic_client

        tool_events = _build_tool_events("tool_1", "Bash", {"command": "rm -rf /"}, index=0)
        text_events = _build_text_events("I cannot do that.")

        set_responses([tool_events, text_events])

        async def deny_permission(name, inp):
            return "BLOCKED: dangerous command"

        tool_results_seen: list[str] = []

        async def mock_tool_end(ctx, result):
            tool_results_seen.append(result)

        with patch("core.rate_limit.check_rate_limit", return_value=(True, "")):
            result = await run(
                messages=[{"role": "user", "content": "delete everything"}],
                tools=[{"name": "Bash", "description": "Run shell", "input_schema": {}}],
                system="test",
                on_text=AsyncMock(),
                on_tool_start=AsyncMock(return_value="ctx"),
                on_tool_end=mock_tool_end,
                on_permission_check=deny_permission,
            )

        # Tool result should contain the error
        assert len(tool_results_seen) == 1
        assert "BLOCKED" in tool_results_seen[0]

    @pytest.mark.asyncio
    async def test_rate_limit_blocks_tool(self, mock_anthropic_client):
        """When rate limit is hit, tool should return error without executing."""
        from core.agent import run

        _, set_responses = mock_anthropic_client

        tool_events = _build_tool_events("tool_1", "Bash", {"command": "ls"}, index=0)
        text_events = _build_text_events("OK")

        set_responses([tool_events, text_events])

        tool_results: list[str] = []

        async def capture_tool_end(ctx, result):
            tool_results.append(result)

        with patch("core.rate_limit.check_rate_limit", return_value=(False, "Rate limited")):
            result = await run(
                messages=[{"role": "user", "content": "list files"}],
                tools=[{"name": "Bash", "description": "Shell", "input_schema": {}}],
                system="test",
                on_text=AsyncMock(),
                on_tool_start=AsyncMock(return_value="ctx"),
                on_tool_end=capture_tool_end,
            )

        assert "Rate limited" in tool_results[0]


# ── Turn limit ──────────────────────────────────────────────────────────

class TestTurnLimit:
    @pytest.mark.asyncio
    async def test_max_turns_enforced(self, mock_anthropic_client):
        """Agent should stop after max_turns even if Claude keeps requesting tools."""
        from core.agent import run

        _, set_responses = mock_anthropic_client

        # Every turn returns a tool call -- should stop after 2 turns
        tool_events = _build_tool_events("t1", "Read", {"file_path": "a.txt"})
        set_responses([tool_events, tool_events, tool_events])

        with patch("core.agent.execute_tool", new_callable=AsyncMock, return_value='{"ok": true}'):
            with patch("core.rate_limit.check_rate_limit", return_value=(True, "")):
                result = await run(
                    messages=[{"role": "user", "content": "loop"}],
                    tools=[{"name": "Read", "description": "Read", "input_schema": {}}],
                    system="test",
                    on_text=AsyncMock(),
                    on_tool_start=AsyncMock(return_value="ctx"),
                    on_tool_end=AsyncMock(),
                    max_turns=2,
                )

        # Should have executed exactly 2 turns of tools
        # Messages: user, assistant+tool, tool_result, assistant+tool, tool_result
        assert len(result) == 5


# ── Hook blocking ───────────────────────────────────────────────────────

class TestHookBlocking:
    @pytest.mark.asyncio
    async def test_pretooluse_hook_blocks_tool(self, mock_anthropic_client, clean_hooks):
        """A PreToolUse hook with non-zero exit should block tool execution."""
        from core.agent import run
        from core.hooks import HookResult

        _, set_responses = mock_anthropic_client

        tool_events = _build_tool_events("t1", "Write", {"file_path": "x.py", "content": "bad"})
        text_events = _build_text_events("OK, blocked.")

        set_responses([tool_events, text_events])

        tool_results: list[str] = []

        async def capture_end(ctx, result):
            tool_results.append(result)

        with (
            patch("core.hooks.has_hooks", return_value=True),
            patch("core.hooks.fire_hooks", new_callable=AsyncMock, return_value=[
                HookResult(exit_code=1, stdout="Lint failed"),
            ]),
            patch("core.rate_limit.check_rate_limit", return_value=(True, "")),
        ):
            await run(
                messages=[{"role": "user", "content": "write code"}],
                tools=[{"name": "Write", "description": "Write file", "input_schema": {}}],
                system="test",
                on_text=AsyncMock(),
                on_tool_start=AsyncMock(return_value="ctx"),
                on_tool_end=capture_end,
                on_permission_check=AsyncMock(return_value=True),
            )

        assert len(tool_results) == 1
        assert "Lint failed" in tool_results[0]


# ── Retry logic ─────────────────────────────────────────────────────────

class TestRetryLogic:
    @pytest.mark.asyncio
    async def test_retries_on_rate_limit(self, mock_anthropic_client):
        """Agent should retry on RateLimitError with exponential backoff."""
        import anthropic as _anthropic
        import httpx
        from core.agent import run

        client, _ = mock_anthropic_client

        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                resp = httpx.Response(429, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
                raise _anthropic.RateLimitError(
                    message="Rate limited",
                    response=resp,
                    body=None,
                )
            return MockStream(_build_text_events("Recovered!"))

        client.messages.stream = MagicMock(side_effect=side_effect)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await run(
                messages=[{"role": "user", "content": "Hi"}],
                tools=[],
                system="test",
                on_text=AsyncMock(),
                on_tool_start=AsyncMock(),
                on_tool_end=AsyncMock(),
            )

        assert call_count == 2
        assert result[-1]["content"][0]["text"] == "Recovered!"


# ── Context compression ────────────────────────────────────────────────

class TestContextCompression:
    @pytest.mark.asyncio
    async def test_compress_called_each_turn(self, mock_anthropic_client):
        """compress_if_needed should be called on each turn of the loop."""
        from core.agent import run

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("Done")])

        with patch("core.agent.compress_if_needed", new_callable=AsyncMock, side_effect=lambda m, s, t: m) as mock_compress:
            await run(
                messages=[{"role": "user", "content": "Hi"}],
                tools=[],
                system="test",
                on_text=AsyncMock(),
                on_tool_start=AsyncMock(),
                on_tool_end=AsyncMock(),
            )

        assert mock_compress.call_count >= 1


# ── Usage recording ─────────────────────────────────────────────────────

class TestUsageRecording:
    @pytest.mark.asyncio
    async def test_usage_recorded_on_success(self, mock_anthropic_client):
        from core.agent import run

        _, set_responses = mock_anthropic_client
        final = MockFinalMessage(usage=MockUsage(input_tokens=150, output_tokens=75))
        set_responses([])

        # Override to include usage
        client, _ = mock_anthropic_client
        client.messages.stream = lambda **kw: MockStream(
            _build_text_events("Hi"), final_message=final,
        )

        with patch("core.agent.record_tokens") as mock_record:
            await run(
                messages=[{"role": "user", "content": "Hi"}],
                tools=[],
                system="test",
                on_text=AsyncMock(),
                on_tool_start=AsyncMock(),
                on_tool_end=AsyncMock(),
            )

        mock_record.assert_called_once()
        args = mock_record.call_args
        assert args[0][1] == 150  # input tokens
        assert args[0][2] == 75   # output tokens
