"""File edit checkpoints — snapshot files before modification for revert.

Stores snapshots in-memory per session, keyed by (file_path, turn_number).
Allows reverting individual files or all changes from a given turn onward.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# session_id -> list of CheckpointEntry (ordered by creation)
_checkpoints: dict[str, list["CheckpointEntry"]] = {}

# session_id -> current turn counter
_turn_counters: dict[str, int] = {}


@dataclass
class CheckpointEntry:
    """A snapshot of a file's content before modification."""

    file_path: str
    turn: int
    content: str | None  # None means file did not exist
    size: int  # bytes


@dataclass
class CheckpointInfo:
    """Summary info about a checkpoint (for listing)."""

    file_path: str
    turn: int
    size: int
    existed: bool


def get_turn(session_id: str) -> int:
    """Get the current turn number for a session."""
    return _turn_counters.get(session_id, 0)


def increment_turn(session_id: str) -> int:
    """Increment and return the new turn number."""
    current = _turn_counters.get(session_id, 0) + 1
    _turn_counters[session_id] = current
    return current


def snapshot_file(session_id: str, file_path: str, turn: int | None = None) -> None:
    """Snapshot a file's current content before it is modified.

    If the file doesn't exist, records that fact so revert can delete it.
    Avoids duplicate snapshots for the same file in the same turn.
    """
    if turn is None:
        turn = get_turn(session_id)

    entries = _checkpoints.setdefault(session_id, [])

    # Skip if we already have a snapshot for this file at this turn
    for entry in entries:
        if entry.file_path == file_path and entry.turn == turn:
            return

    p = Path(file_path)
    if p.is_file():
        try:
            content = p.read_text(encoding="utf-8")
            entries.append(CheckpointEntry(
                file_path=file_path,
                turn=turn,
                content=content,
                size=len(content.encode("utf-8")),
            ))
            logger.debug("Checkpointed %s at turn %d (%d bytes)", file_path, turn, len(content))
        except Exception:
            logger.warning("Failed to snapshot %s", file_path, exc_info=True)
    else:
        # File doesn't exist yet — record so revert can remove it
        entries.append(CheckpointEntry(
            file_path=file_path,
            turn=turn,
            content=None,
            size=0,
        ))
        logger.debug("Checkpointed (new file) %s at turn %d", file_path, turn)


def revert_file(session_id: str, file_path: str, turn: int | None = None) -> bool:
    """Revert a single file to its state at or before *turn*.

    If *turn* is None, reverts to the most recent checkpoint for this file.
    Returns True if a revert was performed.
    """
    entries = _checkpoints.get(session_id, [])
    candidates = [e for e in entries if e.file_path == file_path]
    if turn is not None:
        candidates = [e for e in candidates if e.turn <= turn]

    if not candidates:
        return False

    # Use the earliest snapshot (original state)
    entry = candidates[0]
    return _apply_revert(entry)


def revert_to_turn(session_id: str, turn: int) -> list[str]:
    """Revert all files changed at or after *turn* to their pre-change state.

    Returns list of reverted file paths.
    """
    entries = _checkpoints.get(session_id, [])
    # Find all files that were modified at or after the given turn
    files_to_revert: dict[str, CheckpointEntry] = {}
    for entry in entries:
        if entry.turn >= turn and entry.file_path not in files_to_revert:
            files_to_revert[entry.file_path] = entry

    reverted: list[str] = []
    for file_path, entry in files_to_revert.items():
        if _apply_revert(entry):
            reverted.append(file_path)

    # Remove checkpoint entries at or after the turn
    _checkpoints[session_id] = [e for e in entries if e.turn < turn]

    return reverted


def revert_last(session_id: str) -> list[str]:
    """Revert the most recent turn's changes. Returns list of reverted file paths."""
    entries = _checkpoints.get(session_id, [])
    if not entries:
        return []

    last_turn = max(e.turn for e in entries)
    return revert_to_turn(session_id, last_turn)


def list_checkpoints(session_id: str) -> list[CheckpointInfo]:
    """List all checkpoint entries for a session."""
    entries = _checkpoints.get(session_id, [])
    return [
        CheckpointInfo(
            file_path=e.file_path,
            turn=e.turn,
            size=e.size,
            existed=e.content is not None,
        )
        for e in entries
    ]


def clear_checkpoints(session_id: str) -> None:
    """Clear all checkpoints for a session (e.g., on session destroy)."""
    _checkpoints.pop(session_id, None)
    _turn_counters.pop(session_id, None)


def _apply_revert(entry: CheckpointEntry) -> bool:
    """Apply a single checkpoint revert."""
    p = Path(entry.file_path)
    try:
        if entry.content is None:
            # File didn't exist before — delete it
            if p.exists():
                p.unlink()
                logger.info("Reverted %s (deleted — file was new)", entry.file_path)
            return True
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(entry.content, encoding="utf-8")
            logger.info("Reverted %s to turn %d", entry.file_path, entry.turn)
            return True
    except Exception:
        logger.error("Failed to revert %s", entry.file_path, exc_info=True)
        return False
