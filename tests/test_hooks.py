"""Tests for the hooks system (core/hooks.py)."""

from __future__ import annotations

import sys

import pytest

from core.hooks import HookDef, HookResult, _hooks, fire_hooks, has_hooks, load_hooks


class TestLoadHooks:
    def test_load_valid_config(self, clean_hooks):
        config = {
            "PreToolUse": [{"script": "echo ok", "tools": ["Write"], "timeout": 10}],
            "PostToolUse": [{"script": "echo done"}],
        }
        load_hooks(config)
        assert has_hooks("PreToolUse")
        assert has_hooks("PostToolUse")
        assert not has_hooks("SessionStart")

    def test_load_empty_config(self, clean_hooks):
        load_hooks({})
        assert not has_hooks("PreToolUse")

    def test_load_none_config(self, clean_hooks):
        load_hooks(None)
        assert not has_hooks("PreToolUse")

    def test_invalid_hook_skipped(self, clean_hooks):
        config = {
            "PreToolUse": [{"script": "echo ok"}, {"invalid_field_only": True}],
        }
        load_hooks(config)
        assert has_hooks("PreToolUse")
        assert len(_hooks["PreToolUse"]) == 1  # only valid one loaded

    def test_non_list_value_skipped(self, clean_hooks):
        config = {"PreToolUse": "not a list"}
        load_hooks(config)
        assert not has_hooks("PreToolUse")


class TestFireHooks:
    @pytest.mark.asyncio
    async def test_fire_with_no_hooks(self, clean_hooks):
        results = await fire_hooks("PreToolUse", {"tool_name": "Read"}, tool_name="Read")
        assert results == []

    @pytest.mark.asyncio
    async def test_fire_pretooluse_success(self, clean_hooks):
        # Use a cross-platform command
        cmd = f"{sys.executable} -c \"print('ok')\""
        load_hooks({"PreToolUse": [{"script": cmd}]})
        results = await fire_hooks("PreToolUse", {"tool_name": "Read"}, tool_name="Read")
        assert len(results) == 1
        assert results[0].exit_code == 0
        assert "ok" in results[0].stdout

    @pytest.mark.asyncio
    async def test_pretooluse_nonzero_blocks(self, clean_hooks):
        cmd = f"{sys.executable} -c \"import sys; sys.exit(1)\""
        load_hooks({"PreToolUse": [{"script": cmd}]})
        results = await fire_hooks("PreToolUse", {"tool_name": "Write"}, tool_name="Write")
        assert len(results) == 1
        assert results[0].exit_code == 1

    @pytest.mark.asyncio
    async def test_pretooluse_blocks_subsequent_hooks(self, clean_hooks):
        """When a PreToolUse hook fails, subsequent hooks should NOT fire."""
        fail_cmd = f"{sys.executable} -c \"import sys; sys.exit(1)\""
        ok_cmd = f"{sys.executable} -c \"print('should not run')\""
        load_hooks({"PreToolUse": [
            {"script": fail_cmd},
            {"script": ok_cmd},
        ]})
        results = await fire_hooks("PreToolUse", {}, tool_name="Write")
        assert len(results) == 1  # only the first hook ran

    @pytest.mark.asyncio
    async def test_tool_filter_applies(self, clean_hooks):
        """Hooks with a tools filter should only fire for matching tools."""
        cmd = f"{sys.executable} -c \"print('fired')\""
        load_hooks({"PreToolUse": [{"script": cmd, "tools": ["Write"]}]})

        # Should NOT fire for Read
        results = await fire_hooks("PreToolUse", {}, tool_name="Read")
        assert len(results) == 0

        # Should fire for Write
        results = await fire_hooks("PreToolUse", {}, tool_name="Write")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_timeout_kills_hook(self, clean_hooks):
        """Hook that exceeds timeout should be killed."""
        cmd = f"{sys.executable} -c \"import time; time.sleep(60)\""
        load_hooks({"PreToolUse": [{"script": cmd, "timeout": 1}]})
        results = await fire_hooks("PreToolUse", {}, tool_name="Bash")
        assert len(results) == 1
        assert results[0].exit_code == -1
        assert "timed out" in results[0].stderr.lower()

    @pytest.mark.asyncio
    async def test_posttooluse_all_hooks_fire(self, clean_hooks):
        """PostToolUse hooks should all fire even if one exits non-zero."""
        fail_cmd = f"{sys.executable} -c \"import sys; sys.exit(1)\""
        ok_cmd = f"{sys.executable} -c \"print('ok')\""
        load_hooks({"PostToolUse": [
            {"script": fail_cmd},
            {"script": ok_cmd},
        ]})
        results = await fire_hooks("PostToolUse", {}, tool_name="Write")
        assert len(results) == 2  # both fire for PostToolUse


class TestHasHooks:
    def test_has_hooks_false_when_empty(self, clean_hooks):
        assert not has_hooks("PreToolUse")

    def test_has_hooks_true_after_load(self, clean_hooks):
        load_hooks({"SessionStart": [{"script": "echo hi"}]})
        assert has_hooks("SessionStart")
        assert not has_hooks("SessionEnd")
