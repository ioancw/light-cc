"""Tests for path validation and workspace isolation."""

from __future__ import annotations

import pytest
from pathlib import Path

from core.sandbox import UserWorkspace


@pytest.fixture
def workspace(tmp_path: Path) -> UserWorkspace:
    """Create a UserWorkspace rooted in tmp_path for testing."""
    ws = UserWorkspace.__new__(UserWorkspace)
    ws.user_id = "testuser"
    ws.root = tmp_path / "workspace"
    ws.outputs = tmp_path / "outputs"
    ws.uploads = tmp_path / "uploads"
    ws.memory = tmp_path / "memory"
    for d in (ws.root, ws.outputs, ws.uploads, ws.memory):
        d.mkdir(parents=True, exist_ok=True)
    # Create a test file
    (ws.root / "test.txt").write_text("hello")
    return ws


class TestValidatePath:
    def test_valid_relative_path(self, workspace: UserWorkspace):
        result = workspace.validate_path("test.txt")
        assert result == (workspace.root / "test.txt").resolve()

    def test_valid_absolute_path_in_workspace(self, workspace: UserWorkspace):
        abs_path = str(workspace.root / "test.txt")
        result = workspace.validate_path(abs_path)
        assert result.exists()

    def test_valid_path_in_outputs(self, workspace: UserWorkspace):
        abs_path = str(workspace.outputs / "result.csv")
        result = workspace.validate_path(abs_path)
        assert str(result).startswith(str(workspace.outputs.resolve()))

    def test_traversal_blocked(self, workspace: UserWorkspace):
        with pytest.raises(PermissionError, match="outside your workspace"):
            workspace.validate_path("../../etc/passwd")

    def test_absolute_traversal_blocked(self, workspace: UserWorkspace):
        with pytest.raises(PermissionError, match="outside your workspace"):
            workspace.validate_path("/etc/passwd")

    def test_null_bytes_blocked(self, workspace: UserWorkspace):
        with pytest.raises(PermissionError, match="Null bytes"):
            workspace.validate_path("test\x00.txt")

    def test_windows_traversal_blocked(self, workspace: UserWorkspace):
        with pytest.raises(PermissionError, match="outside your workspace"):
            workspace.validate_path("C:\\Windows\\System32\\config\\SAM")

    def test_dotdot_in_middle(self, workspace: UserWorkspace):
        """Path like workspace/subdir/../../etc should be blocked."""
        with pytest.raises(PermissionError, match="outside your workspace"):
            workspace.validate_path("subdir/../../etc/passwd")
