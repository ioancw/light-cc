"""Tests for core.context -- token estimation, snapshotting, compression, breakdown."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core import context as ctx
from core.config import settings


@pytest.fixture(autouse=True)
def _clear_snapshots():
    ctx._compression_snapshots.clear()
    yield
    ctx._compression_snapshots.clear()


# ── Token estimation (char-based fallback) ──────────────────────────────


class TestEstimation:
    def test_char_estimate_is_chars_div_four(self):
        assert ctx._estimate_tokens("") == 0
        assert ctx._estimate_tokens("abcd") == 1
        assert ctx._estimate_tokens("a" * 400) == 100

    def test_message_estimate_sums_string_contents(self):
        msgs = [
            {"role": "user", "content": "a" * 40},        # ~10 tokens
            {"role": "assistant", "content": "b" * 80},   # ~20 tokens
        ]
        got = ctx._estimate_message_tokens(msgs, system="c" * 20)  # ~5 for system
        assert got == 5 + 10 + 20

    def test_message_estimate_handles_list_content(self):
        msgs = [
            {"role": "user", "content": [
                {"type": "text", "text": "hi"},
                {"type": "tool_result", "content": "ok"},
            ]},
        ]
        # Each block is JSON-serialized then char-divided; just assert it runs + is >0.
        assert ctx._estimate_message_tokens(msgs) > 0


# ── Snapshot / rollback ─────────────────────────────────────────────────


class TestSnapshotRollback:
    def test_snapshot_is_deep_copy(self):
        msgs = [{"role": "user", "content": "hi"}]
        ctx.snapshot_before_compression("cid1", msgs)
        msgs[0]["content"] = "mutated"
        restored = ctx.rollback_compression("cid1")
        assert restored == [{"role": "user", "content": "hi"}]

    def test_rollback_consumes_snapshot(self):
        ctx.snapshot_before_compression("cid1", [{"role": "user", "content": "x"}])
        assert ctx.rollback_compression("cid1") is not None
        assert ctx.rollback_compression("cid1") is None

    def test_rollback_missing_returns_none(self):
        assert ctx.rollback_compression("never-existed") is None


# ── count_message_tokens ────────────────────────────────────────────────


class TestCountTokens:
    @pytest.mark.asyncio
    async def test_sdk_success_returns_input_tokens(self):
        mock_client = SimpleNamespace(
            messages=SimpleNamespace(
                count_tokens=AsyncMock(return_value=SimpleNamespace(input_tokens=4242))
            )
        )
        with patch("core.context.get_client", return_value=mock_client):
            got = await ctx.count_message_tokens(
                [{"role": "user", "content": "hi"}], system="sys",
            )
        assert got == 4242

    @pytest.mark.asyncio
    async def test_sdk_strips_non_api_fields(self):
        """Messages may carry `timestamp`, `model`, etc. -- those must not leak to the API."""
        captured: dict = {}

        async def _capture(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(input_tokens=1)

        mock_client = SimpleNamespace(
            messages=SimpleNamespace(count_tokens=AsyncMock(side_effect=_capture))
        )
        with patch("core.context.get_client", return_value=mock_client):
            await ctx.count_message_tokens(
                [{"role": "user", "content": "hi", "timestamp": 123, "model": "x"}],
                system="s",
            )
        sent = captured["messages"]
        assert sent == [{"role": "user", "content": "hi"}]

    @pytest.mark.asyncio
    async def test_sdk_failure_falls_back_to_estimate(self):
        mock_client = SimpleNamespace(
            messages=SimpleNamespace(count_tokens=AsyncMock(side_effect=RuntimeError("boom")))
        )
        with patch("core.context.get_client", return_value=mock_client):
            got = await ctx.count_message_tokens(
                [{"role": "user", "content": "a" * 400}], system="",
            )
        # 400 chars -> 100 estimated tokens
        assert got == 100


# ── compress_if_needed ──────────────────────────────────────────────────


class TestCompression:
    @pytest.mark.asyncio
    async def test_under_threshold_returns_messages_unchanged(self, monkeypatch):
        msgs = [{"role": "user", "content": "hi"}]
        monkeypatch.setattr(settings, "max_context_tokens", 100_000)
        monkeypatch.setattr(settings, "compression_threshold", 0.8)
        with patch("core.context.count_message_tokens", AsyncMock(return_value=100)):
            got = await ctx.compress_if_needed(msgs, system="sys")
        assert got is msgs  # same object, no work done

    @pytest.mark.asyncio
    async def test_threshold_with_too_few_messages_returns_unchanged(self, monkeypatch):
        """If keep_count >= len(messages), there's nothing to compress."""
        monkeypatch.setattr(settings, "max_context_tokens", 100)
        monkeypatch.setattr(settings, "compression_threshold", 0.1)
        msgs = [{"role": "user", "content": "x"}]
        with patch("core.context.count_message_tokens", AsyncMock(return_value=1000)):
            got = await ctx.compress_if_needed(msgs, system="", keep_recent=4)
        assert got is msgs

    @pytest.mark.asyncio
    async def test_compression_keeps_recent_and_summarizes_old(self, monkeypatch):
        monkeypatch.setattr(settings, "max_context_tokens", 100)
        monkeypatch.setattr(settings, "compression_threshold", 0.1)

        # 10 old messages + 8 recent (keep_recent=4 -> keep_count=8)
        msgs = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg-{i}"}
            for i in range(18)
        ]

        mock_client = SimpleNamespace(
            messages=SimpleNamespace(
                create=AsyncMock(return_value=SimpleNamespace(
                    content=[SimpleNamespace(text="summary-goes-here")]
                ))
            )
        )
        with patch("core.context.get_client", return_value=mock_client), \
             patch("core.context.count_message_tokens", AsyncMock(return_value=9999)):
            got = await ctx.compress_if_needed(msgs, system="sys", keep_recent=4)

        # Output: [summary-user, canned-assistant, ...8 recent]
        assert len(got) == 2 + 8
        assert got[0]["role"] == "user"
        assert "summary-goes-here" in got[0]["content"]
        assert got[1]["role"] == "assistant"
        # The 8 recent messages are preserved from the tail, unchanged
        assert got[2:] == msgs[-8:]

    @pytest.mark.asyncio
    async def test_compression_summarizer_failure_returns_original(self, monkeypatch):
        monkeypatch.setattr(settings, "max_context_tokens", 100)
        monkeypatch.setattr(settings, "compression_threshold", 0.1)

        msgs = [{"role": "user", "content": f"m{i}"} for i in range(20)]

        mock_client = SimpleNamespace(
            messages=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("api down")))
        )
        with patch("core.context.get_client", return_value=mock_client), \
             patch("core.context.count_message_tokens", AsyncMock(return_value=9999)):
            got = await ctx.compress_if_needed(msgs, system="sys", keep_recent=4)

        assert got == msgs


# ── Breakdown ───────────────────────────────────────────────────────────


class TestContextBreakdown:
    @pytest.mark.asyncio
    async def test_breakdown_reports_each_component(self, monkeypatch):
        monkeypatch.setattr(settings, "max_context_tokens", 200_000)
        with patch("core.context.count_message_tokens", AsyncMock(return_value=50_000)):
            got = await ctx.get_context_breakdown(
                messages=[{"role": "user", "content": "a" * 40}],
                system="s" * 80,
                tools=[{"name": "t"}],
                project_config="p" * 40,
                rules_text="r" * 40,
                memory_context="m" * 40,
                skill_prompt="k" * 40,
            )
        assert got["system_prompt_tokens"] == 20
        assert got["project_config_tokens"] == 10
        assert got["rules_tokens"] == 10
        assert got["memory_tokens"] == 10
        assert got["skill_tokens"] == 10
        assert got["messages_tokens"] == 10
        assert got["total_tokens"] == 50_000
        assert got["max_tokens"] == 200_000
        assert got["usage_pct"] == 25.0

    @pytest.mark.asyncio
    async def test_breakdown_zero_max_tokens_does_not_divide_by_zero(self, monkeypatch):
        monkeypatch.setattr(settings, "max_context_tokens", 0)
        with patch("core.context.count_message_tokens", AsyncMock(return_value=10)):
            got = await ctx.get_context_breakdown(
                messages=[{"role": "user", "content": "x"}], system="",
            )
        assert got["usage_pct"] == 0


# ── _format_for_summary ─────────────────────────────────────────────────


class TestFormatForSummary:
    def test_renders_string_content(self):
        out = ctx._format_for_summary([
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ])
        assert "user: hello" in out
        assert "assistant: hi" in out

    def test_renders_tool_use_and_tool_result_blocks(self):
        out = ctx._format_for_summary([
            {"role": "assistant", "content": [
                {"type": "text", "text": "thinking"},
                {"type": "tool_use", "name": "Read", "id": "x"},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "content": "file contents"},
            ]},
        ])
        assert "thinking" in out
        assert "[called tool Read]" in out
        assert "[tool result: file contents]" in out

    def test_truncates_long_text_to_500_chars(self):
        long = "x" * 2000
        out = ctx._format_for_summary([{"role": "user", "content": long}])
        # The "user: " prefix + up to 500 chars of payload
        assert "user: " + "x" * 500 in out
        # And the 501st char is not present on its own
        assert "x" * 600 not in out

    def test_caps_at_100_entries(self):
        msgs = [{"role": "user", "content": str(i)} for i in range(200)]
        out = ctx._format_for_summary(msgs)
        assert out.count("\n") == 99  # 100 lines, 99 separators
