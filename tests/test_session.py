"""
Unit tests for session persistence.

Overall purpose:
  Verify MountSession save/load/clear round-trips and invalid JSON handling
  without touching the real Application Support path.

Requirements:
  pytest.
"""

from __future__ import annotations

import json
from pathlib import Path

from dislocker_ui.session import MountSession, clear_session, load_session, save_session


def _sample_session() -> MountSession:
    """Return a representative MountSession for tests."""
    return MountSession(
        volume="/dev/disk2s1",
        fuse_mount="/tmp/dislocker-ui-test",
        dislocker_file="/tmp/dislocker-ui-test/dislocker-file",
        raw_disk="/dev/disk3",
        ntfs_mount="/Volumes/BitLocker",
        readonly=True,
        used_ntfs3g=False,
    )


def test_save_load_roundtrip(tmp_path: Path) -> None:
    """Saved sessions reload with identical fields."""
    path = tmp_path / "active_session.json"
    session = _sample_session()
    written = save_session(session, path)
    assert written == path
    loaded = load_session(path)
    assert loaded == session


def test_load_missing_returns_none(tmp_path: Path) -> None:
    """Missing session files yield None."""
    assert load_session(tmp_path / "missing.json") is None


def test_load_invalid_json_returns_none(tmp_path: Path) -> None:
    """Corrupt JSON yields None instead of raising."""
    path = tmp_path / "bad.json"
    path.write_text("{not-json", encoding="utf-8")
    assert load_session(path) is None


def test_load_incomplete_object_returns_none(tmp_path: Path) -> None:
    """JSON objects missing required fields yield None."""
    path = tmp_path / "partial.json"
    path.write_text(json.dumps({"volume": "/dev/disk1"}), encoding="utf-8")
    assert load_session(path) is None


def test_clear_session_removes_file(tmp_path: Path) -> None:
    """clear_session deletes an existing session file."""
    path = tmp_path / "active_session.json"
    save_session(_sample_session(), path)
    assert path.is_file()
    clear_session(path)
    assert not path.exists()


def test_clear_session_missing_is_ok(tmp_path: Path) -> None:
    """clear_session is a no-op when the file is already gone."""
    clear_session(tmp_path / "nope.json")
