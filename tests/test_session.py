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
from unittest.mock import patch

from dislocker_ui.session import (
    MountSession,
    active_session_path_for_user,
    clear_session,
    legacy_session_present,
    load_session,
    root_log_path,
    root_session_path,
    root_state_dir,
    save_session,
)


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


def test_legacy_session_probe_identifies_unversioned_session(tmp_path: Path) -> None:
    """Pre-0.3.0 state is identified for manual recovery, never loaded."""
    path = tmp_path / "active_session.json"
    path.write_text(json.dumps({"ntfs_mount": "/Volumes/Old"}), encoding="utf-8")
    assert load_session(path) is None
    assert legacy_session_present(path) is True


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


def test_root_state_paths_are_fixed_per_uid(tmp_path: Path) -> None:
    """Canonical privileged state is derived from UID, never request content."""
    assert root_state_dir(501, state_root=tmp_path) == tmp_path / "501"
    assert root_session_path(501, state_root=tmp_path) == tmp_path / "501" / "active_session.json"
    assert root_log_path(501, state_root=tmp_path) == tmp_path / "501" / "operation.log"


def test_active_session_path_uses_root_state_for_unprivileged_macos() -> None:
    """The macOS GUI reads the root helper's canonical status file."""
    with (
        patch("dislocker_ui.session.sys.platform", "darwin"),
        patch("dislocker_ui.session.os.geteuid", return_value=501),
        patch("dislocker_ui.session.os.getuid", return_value=501),
    ):
        assert active_session_path_for_user() == root_session_path(501)


def test_save_elevated_session_sets_group_before_replace(tmp_path: Path) -> None:
    """Elevated state receives its reader group while still in the temp inode."""
    session = _sample_session()
    session.elevated = True
    with patch("dislocker_ui.session.os.fchown") as fchown:
        save_session(session, tmp_path / "active_session.json", owner_gid=20)
    assert fchown.call_args.args[1:] == (0, 20)


def test_root_state_path_rejects_negative_uid() -> None:
    """UID validation prevents traversal-like canonical state construction."""
    import pytest

    with pytest.raises(ValueError, match="negative"):
        root_state_dir(-1)
