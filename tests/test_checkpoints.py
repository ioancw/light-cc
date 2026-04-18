"""Tests for core.checkpoints -- per-session file snapshot + revert."""

from __future__ import annotations

from pathlib import Path

import pytest

from core import checkpoints as cp


@pytest.fixture(autouse=True)
def _isolate_checkpoint_state():
    """Each test gets a clean global state."""
    cp._checkpoints.clear()
    cp._turn_counters.clear()
    yield
    cp._checkpoints.clear()
    cp._turn_counters.clear()


@pytest.fixture
def session_id() -> str:
    return "sess-test"


# ── Turn counter ────────────────────────────────────────────────────────


class TestTurnCounter:
    def test_initial_turn_is_zero(self, session_id):
        assert cp.get_turn(session_id) == 0

    def test_increment_returns_new_value(self, session_id):
        assert cp.increment_turn(session_id) == 1
        assert cp.increment_turn(session_id) == 2
        assert cp.get_turn(session_id) == 2

    def test_counters_per_session(self):
        cp.increment_turn("a")
        cp.increment_turn("a")
        cp.increment_turn("b")
        assert cp.get_turn("a") == 2
        assert cp.get_turn("b") == 1


# ── Snapshotting ────────────────────────────────────────────────────────


class TestSnapshot:
    def test_snapshot_existing_file(self, tmp_path: Path, session_id):
        f = tmp_path / "a.txt"
        f.write_text("original", encoding="utf-8")
        cp.snapshot_file(session_id, str(f), turn=1)

        entries = cp._checkpoints[session_id]
        assert len(entries) == 1
        assert entries[0].content == "original"
        assert entries[0].size == len("original")

    def test_snapshot_nonexistent_file_records_none(self, tmp_path: Path, session_id):
        f = tmp_path / "new.txt"  # does not exist yet
        cp.snapshot_file(session_id, str(f), turn=1)

        entries = cp._checkpoints[session_id]
        assert len(entries) == 1
        assert entries[0].content is None
        assert entries[0].size == 0

    def test_duplicate_snapshot_same_turn_skipped(self, tmp_path: Path, session_id):
        f = tmp_path / "a.txt"
        f.write_text("one", encoding="utf-8")
        cp.snapshot_file(session_id, str(f), turn=1)
        f.write_text("two", encoding="utf-8")  # change between snapshots
        cp.snapshot_file(session_id, str(f), turn=1)

        entries = cp._checkpoints[session_id]
        assert len(entries) == 1
        # Original "one" is preserved, not overwritten by "two"
        assert entries[0].content == "one"

    def test_snapshot_uses_current_turn_when_none(self, tmp_path: Path, session_id):
        cp.increment_turn(session_id)  # turn -> 1
        cp.increment_turn(session_id)  # turn -> 2
        f = tmp_path / "a.txt"
        f.write_text("x", encoding="utf-8")
        cp.snapshot_file(session_id, str(f))

        assert cp._checkpoints[session_id][0].turn == 2


# ── Revert single file ──────────────────────────────────────────────────


class TestRevertFile:
    def test_revert_restores_original_content(self, tmp_path: Path, session_id):
        f = tmp_path / "a.txt"
        f.write_text("original", encoding="utf-8")
        cp.snapshot_file(session_id, str(f), turn=1)

        f.write_text("modified", encoding="utf-8")
        assert cp.revert_file(session_id, str(f)) is True
        assert f.read_text(encoding="utf-8") == "original"

    def test_revert_deletes_file_that_was_new(self, tmp_path: Path, session_id):
        f = tmp_path / "new.txt"
        cp.snapshot_file(session_id, str(f), turn=1)  # file does not exist at snapshot
        f.write_text("created by agent", encoding="utf-8")

        assert cp.revert_file(session_id, str(f)) is True
        assert not f.exists()

    def test_revert_no_snapshot_returns_false(self, tmp_path: Path, session_id):
        assert cp.revert_file(session_id, str(tmp_path / "nope.txt")) is False

    def test_revert_uses_earliest_snapshot_across_turns(self, tmp_path: Path, session_id):
        f = tmp_path / "a.txt"
        f.write_text("v0", encoding="utf-8")
        cp.snapshot_file(session_id, str(f), turn=1)
        f.write_text("v1", encoding="utf-8")
        cp.snapshot_file(session_id, str(f), turn=2)
        f.write_text("v2", encoding="utf-8")

        assert cp.revert_file(session_id, str(f)) is True
        assert f.read_text(encoding="utf-8") == "v0"


# ── Revert by turn ──────────────────────────────────────────────────────


class TestRevertToTurn:
    def test_reverts_only_files_from_target_turn_onward(self, tmp_path: Path, session_id):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        c = tmp_path / "c.txt"
        a.write_text("a-orig", encoding="utf-8")
        b.write_text("b-orig", encoding="utf-8")
        c.write_text("c-orig", encoding="utf-8")
        cp.snapshot_file(session_id, str(a), turn=1)
        cp.snapshot_file(session_id, str(b), turn=2)
        cp.snapshot_file(session_id, str(c), turn=3)

        a.write_text("a-mod", encoding="utf-8")
        b.write_text("b-mod", encoding="utf-8")
        c.write_text("c-mod", encoding="utf-8")

        reverted = cp.revert_to_turn(session_id, turn=2)

        assert set(reverted) == {str(b), str(c)}
        assert a.read_text(encoding="utf-8") == "a-mod"  # turn 1, untouched
        assert b.read_text(encoding="utf-8") == "b-orig"
        assert c.read_text(encoding="utf-8") == "c-orig"

    def test_reverted_entries_removed_from_session(self, tmp_path: Path, session_id):
        f = tmp_path / "a.txt"
        f.write_text("x", encoding="utf-8")
        cp.snapshot_file(session_id, str(f), turn=1)
        cp.snapshot_file(session_id, str(f), turn=2)

        cp.revert_to_turn(session_id, turn=2)
        remaining_turns = [e.turn for e in cp._checkpoints[session_id]]
        assert remaining_turns == [1]

    def test_revert_last_targets_highest_turn(self, tmp_path: Path, session_id):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("a-orig", encoding="utf-8")
        b.write_text("b-orig", encoding="utf-8")
        cp.snapshot_file(session_id, str(a), turn=1)
        cp.snapshot_file(session_id, str(b), turn=2)

        a.write_text("a-mod", encoding="utf-8")
        b.write_text("b-mod", encoding="utf-8")

        reverted = cp.revert_last(session_id)
        assert reverted == [str(b)]
        assert a.read_text(encoding="utf-8") == "a-mod"
        assert b.read_text(encoding="utf-8") == "b-orig"

    def test_revert_last_on_empty_session(self, session_id):
        assert cp.revert_last(session_id) == []


# ── Listing + clearing ──────────────────────────────────────────────────


class TestListAndClear:
    def test_list_checkpoints_summary(self, tmp_path: Path, session_id):
        existing = tmp_path / "a.txt"
        existing.write_text("hi", encoding="utf-8")
        new_file = tmp_path / "b.txt"
        cp.snapshot_file(session_id, str(existing), turn=1)
        cp.snapshot_file(session_id, str(new_file), turn=2)

        infos = cp.list_checkpoints(session_id)
        assert len(infos) == 2
        by_path = {i.file_path: i for i in infos}
        assert by_path[str(existing)].existed is True
        assert by_path[str(new_file)].existed is False
        assert by_path[str(new_file)].size == 0

    def test_clear_removes_session_state(self, tmp_path: Path, session_id):
        f = tmp_path / "a.txt"
        f.write_text("x", encoding="utf-8")
        cp.snapshot_file(session_id, str(f), turn=1)
        cp.increment_turn(session_id)

        cp.clear_checkpoints(session_id)
        assert session_id not in cp._checkpoints
        assert cp.get_turn(session_id) == 0

    def test_clear_is_safe_on_unknown_session(self):
        cp.clear_checkpoints("never-existed")  # should not raise
