"""
Focused tests for runner elevation facade and BEK canonicalization.

Overall purpose:
  Verify mount/unmount dispatch to elevate when needed, ntfs-3g option assembly
  (covered in test_runner_ntfs), and BEK absolute-path handling — all mocked.

Requirements:
  pytest.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dislocker_ui.deps import DepsStatus
from dislocker_ui.runner import (
    MountRequest,
    RunnerError,
    UnlockMethod,
    _canonicalize_bek_secret,
    mount_volume,
    unmount_volume,
)
from dislocker_ui.session import MountSession


def _deps() -> DepsStatus:
    return DepsStatus(
        dislocker_fuse="/bin/dislocker-fuse",
        hdiutil="/bin/hdiutil",
        diskutil="/bin/diskutil",
        umount="/bin/umount",
        ntfs3g="/bin/ntfs-3g",
    )


def test_mount_dispatches_to_elevate_when_needed() -> None:
    """Unprivileged Darwin path calls run_elevated_mount, not in-process."""
    req = MountRequest(
        volume="/dev/disk2s1",
        method=UnlockMethod.USER_PASSWORD,
        secret="x",
        readonly=True,
    )
    session = MagicMock(spec=MountSession)
    with (
        patch("dislocker_ui.elevate.needs_elevation", return_value=True),
        patch("dislocker_ui.runner.load_session", return_value=None),
        patch(
            "dislocker_ui.elevate.prepare_elevation_paths",
            return_value=(Path("/tmp/s.json"), Path("/tmp/l.log")),
        ),
        patch("dislocker_ui.elevate.run_elevated_mount", return_value=session) as elev,
        patch("dislocker_ui.runner._mount_in_process") as inproc,
    ):
        result = mount_volume(req, _deps(), lambda _m: None)
    assert result is session
    elev.assert_called_once()
    inproc.assert_not_called()


def test_mount_rejects_active_session_before_elevating() -> None:
    """An active session raises before any admin prompt is requested."""
    req = MountRequest(
        volume="/dev/disk2s1",
        method=UnlockMethod.USER_PASSWORD,
        secret="x",
        readonly=True,
    )
    existing = MountSession(
        volume="/dev/disk2s1",
        fuse_mount="/tmp/f",
        dislocker_file="/tmp/f/dislocker-file",
        raw_disk="/dev/disk9",
        ntfs_mount="/Volumes/X",
        readonly=True,
        used_ntfs3g=True,
        elevated=True,
    )
    with (
        patch("dislocker_ui.elevate.needs_elevation", return_value=True),
        patch("dislocker_ui.runner.load_session", return_value=existing),
        patch("dislocker_ui.elevate.run_elevated_mount") as elev,
        patch("dislocker_ui.elevate.prepare_elevation_paths") as prep,
        pytest.raises(RunnerError, match="already active"),
    ):
        mount_volume(req, _deps(), lambda _m: None)
    elev.assert_not_called()
    prep.assert_not_called()


def test_mount_in_process_when_already_root() -> None:
    """When needs_elevation is false, in-process path runs."""
    req = MountRequest(
        volume="/dev/disk2s1",
        method=UnlockMethod.USER_PASSWORD,
        secret="x",
        readonly=True,
    )
    session = MagicMock(spec=MountSession)
    with (
        patch("dislocker_ui.elevate.needs_elevation", return_value=False),
        patch("dislocker_ui.runner._mount_in_process", return_value=session) as inproc,
        patch("dislocker_ui.elevate.run_elevated_mount") as elev,
    ):
        result = mount_volume(req, _deps(), lambda _m: None)
    assert result is session
    inproc.assert_called_once()
    elev.assert_not_called()


def test_unmount_reelevates_when_session_elevated() -> None:
    """Elevated sessions re-prompt via run_elevated_unmount."""
    session = MountSession(
        volume="/dev/disk2s1",
        fuse_mount="/tmp/f",
        dislocker_file="/tmp/f/dislocker-file",
        raw_disk="/dev/disk9",
        ntfs_mount="/Volumes/X",
        readonly=True,
        used_ntfs3g=True,
        elevated=True,
    )
    with (
        patch("dislocker_ui.runner.load_session", return_value=session),
        patch("dislocker_ui.elevate.needs_elevation", return_value=True),
        patch(
            "dislocker_ui.elevate.prepare_elevation_paths",
            return_value=(Path("/tmp/s.json"), Path("/tmp/l.log")),
        ),
        patch("dislocker_ui.elevate.run_elevated_unmount") as elev,
    ):
        unmount_volume(_deps(), lambda _m: None)
    elev.assert_called_once()


def test_bek_canonicalize_absolute_skips_expanduser(tmp_path: Path) -> None:
    """Absolute BEK paths are resolve()'d without expanduser."""
    bek = tmp_path / "key.bek"
    bek.write_bytes(b"x")
    req = MountRequest(
        volume="/dev/disk2s1",
        method=UnlockMethod.BEK_FILE,
        secret=str(bek),
        readonly=True,
    )
    with patch.object(Path, "expanduser", side_effect=AssertionError("no expanduser")):
        out = _canonicalize_bek_secret(req)
    assert out.secret == str(bek.resolve())


def test_bek_canonicalize_tilde_expands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Relative/tilde BEK paths expand in the parent path."""
    home = tmp_path / "home"
    home.mkdir()
    bek = home / "key.bek"
    bek.write_bytes(b"x")
    monkeypatch.setenv("HOME", str(home))
    req = MountRequest(
        volume="/dev/disk2s1",
        method=UnlockMethod.BEK_FILE,
        secret="~/key.bek",
        readonly=True,
    )
    out = _canonicalize_bek_secret(req)
    assert out.secret == str(bek.resolve())
    assert "~" not in out.secret
