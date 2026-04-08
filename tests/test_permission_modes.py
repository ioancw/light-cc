"""Tests for permission modes (core/permission_modes.py)."""

from __future__ import annotations

import pytest

from core.permission_modes import PermissionMode, check_permission, READ_ONLY_TOOLS, FILE_EDIT_TOOLS


class TestPermissionModeCycling:
    def test_default_to_auto_edit(self):
        assert PermissionMode.DEFAULT.next() == PermissionMode.AUTO_EDIT

    def test_auto_edit_to_plan(self):
        assert PermissionMode.AUTO_EDIT.next() == PermissionMode.PLAN

    def test_plan_to_auto(self):
        assert PermissionMode.PLAN.next() == PermissionMode.AUTO

    def test_auto_wraps_to_default(self):
        assert PermissionMode.AUTO.next() == PermissionMode.DEFAULT

    def test_full_cycle(self):
        mode = PermissionMode.DEFAULT
        seen = [mode]
        for _ in range(4):
            mode = mode.next()
            seen.append(mode)
        assert seen[0] == seen[-1]  # wraps around
        assert len(set(seen[:-1])) == 4  # all 4 modes visited


class TestBlockedCommands:
    """Blocked commands should be denied in ALL modes."""

    @pytest.mark.parametrize("mode", list(PermissionMode))
    def test_blocked_bash_denied(self, mode):
        result = check_permission(mode, "Bash", {"command": "rm -rf /"})
        assert isinstance(result, str)
        assert "BLOCKED" in result

    @pytest.mark.parametrize("mode", list(PermissionMode))
    def test_blocked_mkfs_denied(self, mode):
        result = check_permission(mode, "Bash", {"command": "mkfs.ext4 /dev/sda"})
        assert isinstance(result, str)
        assert "BLOCKED" in result


class TestDefaultMode:
    def test_read_only_auto_approved(self):
        for tool in READ_ONLY_TOOLS:
            result = check_permission(PermissionMode.DEFAULT, tool, {})
            assert result is True, f"{tool} should be auto-approved in DEFAULT"

    def test_file_edit_asks_user(self):
        for tool in FILE_EDIT_TOOLS:
            result = check_permission(PermissionMode.DEFAULT, tool, {"file_path": "test.py", "content": "x"})
            assert result is None, f"{tool} should ask user in DEFAULT"

    def test_risky_bash_asks_user(self):
        result = check_permission(PermissionMode.DEFAULT, "Bash", {"command": "rm -rf ./build"})
        assert result is None  # risky, ask user

    def test_safe_bash_auto_approved(self):
        result = check_permission(PermissionMode.DEFAULT, "Bash", {"command": "echo hello"})
        assert result is True

    def test_safe_tool_auto_approved(self):
        result = check_permission(PermissionMode.DEFAULT, "PythonExec", {"code": "print(1)"})
        assert result is True


class TestAutoEditMode:
    def test_read_only_auto_approved(self):
        for tool in READ_ONLY_TOOLS:
            result = check_permission(PermissionMode.AUTO_EDIT, tool, {})
            assert result is True

    def test_file_edit_auto_approved(self):
        for tool in FILE_EDIT_TOOLS:
            result = check_permission(PermissionMode.AUTO_EDIT, tool, {"file_path": "x.py", "content": "y"})
            assert result is True, f"{tool} should be auto-approved in AUTO_EDIT"

    def test_risky_bash_asks_user(self):
        result = check_permission(PermissionMode.AUTO_EDIT, "Bash", {"command": "rm -rf ./build"})
        assert result is None

    def test_safe_bash_auto_approved(self):
        result = check_permission(PermissionMode.AUTO_EDIT, "Bash", {"command": "ls -la"})
        assert result is True


class TestPlanMode:
    def test_read_only_allowed(self):
        for tool in READ_ONLY_TOOLS:
            result = check_permission(PermissionMode.PLAN, tool, {})
            assert result is True, f"{tool} should be allowed in PLAN"

    def test_file_edit_denied(self):
        for tool in FILE_EDIT_TOOLS:
            result = check_permission(PermissionMode.PLAN, tool, {"file_path": "x", "content": "y"})
            assert isinstance(result, str)
            assert "PLAN MODE" in result

    def test_bash_denied(self):
        result = check_permission(PermissionMode.PLAN, "Bash", {"command": "echo hi"})
        assert isinstance(result, str)
        assert "PLAN MODE" in result

    def test_python_exec_denied(self):
        result = check_permission(PermissionMode.PLAN, "PythonExec", {"code": "print(1)"})
        assert isinstance(result, str)


class TestAutoMode:
    def test_everything_auto_approved(self):
        tools_and_inputs = [
            ("Read", {}),
            ("Write", {"file_path": "x", "content": "y"}),
            ("Edit", {"file_path": "x", "old_string": "a", "new_string": "b"}),
            ("Bash", {"command": "echo hello"}),
            ("PythonExec", {"code": "print(1)"}),
            ("Glob", {"pattern": "*.py"}),
            ("Grep", {"pattern": "foo"}),
        ]
        for tool, inp in tools_and_inputs:
            result = check_permission(PermissionMode.AUTO, tool, inp)
            assert result is True, f"{tool} should be auto-approved in AUTO"

    def test_blocked_still_denied(self):
        result = check_permission(PermissionMode.AUTO, "Bash", {"command": "rm -rf /"})
        assert isinstance(result, str)
        assert "BLOCKED" in result
