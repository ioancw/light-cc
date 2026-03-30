"""Tests for permission system."""

from __future__ import annotations

import pytest
from core.permissions import is_blocked, is_risky


class TestBlocked:
    def test_rm_rf_root_blocked(self):
        assert is_blocked("bash", {"command": "rm -rf /"})

    def test_rm_rf_root_star_blocked(self):
        assert is_blocked("bash", {"command": "rm -rf /*"})

    def test_mkfs_blocked(self):
        assert is_blocked("bash", {"command": "mkfs.ext4 /dev/sda1"})

    def test_fork_bomb_blocked(self):
        assert is_blocked("bash", {"command": ":(){ :|:& };:"})

    def test_normal_command_not_blocked(self):
        assert not is_blocked("bash", {"command": "ls -la"})

    def test_non_bash_not_blocked(self):
        assert not is_blocked("read", {"file_path": "/etc/passwd"})


class TestRisky:
    def test_rm_rf_risky(self):
        assert is_risky("bash", {"command": "rm -rf my_dir"})

    def test_drop_table_risky(self):
        assert is_risky("bash", {"command": "psql -c 'DROP TABLE users'"})

    def test_kill_risky(self):
        assert is_risky("bash", {"command": "kill -9 12345"})

    def test_normal_command_not_risky(self):
        assert not is_risky("bash", {"command": "echo hello"})

    def test_write_to_system_path_risky(self):
        assert is_risky("write", {"file_path": "/etc/hosts", "content": "..."})

    def test_write_to_windows_system_risky(self):
        assert is_risky("write", {"file_path": "C:\\Windows\\System32\\config", "content": "..."})

    def test_write_to_user_dir_not_risky(self):
        assert not is_risky("write", {"file_path": "/home/user/test.txt", "content": "..."})

    def test_python_exec_with_os_system_risky(self):
        assert is_risky("python_exec", {"script": "import os; os.system('rm -rf /')"})

    def test_python_exec_with_subprocess_risky(self):
        assert is_risky("python_exec", {"script": "import subprocess; subprocess.run(['ls'])"})

    def test_python_exec_normal_not_risky(self):
        assert not is_risky("python_exec", {"script": "print('hello world')"})


class TestPascalCaseNames:
    """Verify PascalCase canonical names work in permission checks."""

    def test_bash_blocked_pascal(self):
        assert is_blocked("Bash", {"command": "rm -rf /"})

    def test_bash_risky_pascal(self):
        assert is_risky("Bash", {"command": "rm -rf my_dir"})

    def test_write_risky_pascal(self):
        assert is_risky("Write", {"file_path": "/etc/hosts", "content": "..."})

    def test_python_exec_risky_pascal(self):
        assert is_risky("PythonExec", {"script": "import os; os.system('rm -rf /')"})


class TestBypassPrevention:
    """Verify that shell injection / bypass attempts are caught."""

    def test_newline_injection_blocked(self):
        assert is_blocked("bash", {"command": "echo safe\nrm -rf /"})

    def test_newline_injection_risky(self):
        assert is_risky("bash", {"command": "echo safe\nrm -rf mydir"})

    def test_semicolon_chain_blocked(self):
        assert is_blocked("bash", {"command": "echo safe; rm -rf /"})

    def test_and_chain_blocked(self):
        assert is_blocked("bash", {"command": "echo safe && rm -rf /"})

    def test_or_chain_blocked(self):
        assert is_blocked("bash", {"command": "echo safe || rm -rf /"})

    def test_backtick_subshell_blocked(self):
        assert is_blocked("bash", {"command": "echo `rm -rf /`"})

    def test_dollar_subshell_blocked(self):
        assert is_blocked("bash", {"command": "echo $(rm -rf /)"})

    def test_carriage_return_injection(self):
        assert is_blocked("bash", {"command": "echo safe\rrm -rf /"})

    def test_null_byte_injection(self):
        assert is_blocked("bash", {"command": "echo safe\x00rm -rf /"})

    def test_clean_command_still_allowed(self):
        assert not is_blocked("bash", {"command": "echo hello && ls -la"})
        assert not is_risky("bash", {"command": "echo hello && ls -la"})
