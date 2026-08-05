"""
Focused tests for runner elevation facade and BEK canonicalization.

Overall purpose:
  Verify mount/unmount dispatch to elevate when needed, ntfs-3g option assembly
  (covered in test_runner_ntfs), and BEK absolute-path handling — all mocked.

Requirements:
  pytest.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dislocker_ui.deps import DepsStatus
from dislocker_ui.mount_policy import (
    allocate_privileged_fuse_path,
    remove_empty_privileged_staging_parents,
)
from dislocker_ui.runner import (
    MountRequest,
    RunnerError,
    UnlockMethod,
    _canonicalize_bek_secret,
    _wait_for_file,
    mount_volume,
    unmount_volume,
)
from dislocker_ui.session import MountSession


def _fake_elevation_transaction(session_path: Path, log_path: Path):
    """Return a callable matching elevate.elevation_transaction for facade tests."""

    @contextlib.contextmanager
    def _cm() -> Iterator[tuple[Path, Path]]:
        yield session_path, log_path

    return _cm


def _deps() -> DepsStatus:
    return DepsStatus(
        dislocker_fuse="/bin/dislocker-fuse",
        hdiutil="/bin/hdiutil",
        diskutil="/bin/diskutil",
        umount="/bin/umount",
        ntfs3g="/bin/ntfs-3g",
    )


def _log(_message: str) -> None:
    """Discard log output in runner facade tests."""


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
            "dislocker_ui.elevate.elevation_transaction",
            _fake_elevation_transaction(Path("/tmp/s.json"), Path("/tmp/l.log")),
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
    deps = _deps()
    with (  # noqa: SIM117 (keep pytest.raises scoped to the single call under test)
        patch("dislocker_ui.elevate.needs_elevation", return_value=True),
        patch("dislocker_ui.runner.load_session", return_value=existing),
        patch("dislocker_ui.elevate.run_elevated_mount") as elev,
        patch("dislocker_ui.elevate.elevation_transaction") as txn,
    ):
        with pytest.raises(RunnerError, match="already active"):
            mount_volume(req, deps, lambda _m: None)
    elev.assert_not_called()
    txn.assert_not_called()


def test_mount_rejects_legacy_session_before_elevating() -> None:
    """A pre-versioned session cannot be overwritten by a new elevated mount."""
    req = MountRequest(
        volume="/dev/disk2s1",
        method=UnlockMethod.USER_PASSWORD,
        secret="x",
        readonly=True,
    )
    deps = _deps()
    with (
        patch("dislocker_ui.elevate.needs_elevation", return_value=True),
        patch("dislocker_ui.runner.load_session", return_value=None),
        patch("dislocker_ui.runner.legacy_session_present", return_value=True),
        patch("dislocker_ui.elevate.run_elevated_mount") as elev,
        pytest.raises(RunnerError, match=r"pre-0\.3\.0"),
    ):
        mount_volume(req, deps, _log)
    elev.assert_not_called()


def test_unmount_rejects_legacy_session_with_recovery_guidance() -> None:
    """An unversioned session record is not treated as absent mount state."""
    deps = _deps()
    with (
        patch("dislocker_ui.elevate.needs_elevation", return_value=False),
        patch("dislocker_ui.runner.load_session", return_value=None),
        patch("dislocker_ui.runner.legacy_session_present", return_value=True),
        pytest.raises(RunnerError, match=r"pre-0\.3\.0"),
    ):
        unmount_volume(deps, _log)


def test_unmount_uses_canonical_path_for_all_cleanup_state() -> None:
    """A Darwin unmount cleans the same canonical state file it loaded."""
    canonical = Path("/var/db/dislocker-ui/501/active_session.json")
    session = MountSession(
        volume="/dev/disk2s1",
        fuse_mount="/tmp/fuse",
        dislocker_file="/tmp/fuse/dislocker-file",
        raw_disk="/dev/disk9",
        ntfs_mount="/Volumes/X",
        readonly=True,
        used_ntfs3g=True,
    )
    with (
        patch("dislocker_ui.elevate.needs_elevation", return_value=True),
        patch("dislocker_ui.runner.active_session_path_for_user", return_value=canonical),
        patch("dislocker_ui.runner.load_session", return_value=session),
        patch("dislocker_ui.runner._unmount_ntfs", return_value=[]),
        patch("dislocker_ui.runner._detach_raw_disk", return_value=[]),
        patch("dislocker_ui.runner._unmount_fuse", return_value=[]),
        patch("dislocker_ui.runner._remove_fuse_dir", return_value=[]) as remove_fuse,
        patch("dislocker_ui.runner._remove_empty_ntfs_dir", return_value=[]),
        patch("dislocker_ui.runner.clear_session") as clear,
    ):
        unmount_volume(_deps(), lambda _m: None)
    assert remove_fuse.call_args.kwargs["session_path"] == canonical
    clear.assert_called_once_with(canonical)


def test_elevated_fuse_failure_names_the_diagnostic_log() -> None:
    """Elevated FUSE failures preserve a useful diagnostic location."""
    proc = MagicMock()
    proc.poll.return_value = 1
    proc.stdout = None
    with pytest.raises(RunnerError, match=r"operation\.log"):
        _wait_for_file(
            Path("/tmp/missing-dislocker-file"),
            proc,
            lambda _m: None,
            timeout_s=1,
            diagnostic_log_path=Path("/var/db/dislocker-ui/501/operation.log"),
        )


def test_privileged_staging_is_private_and_empty_parents_are_removed(tmp_path: Path) -> None:
    """Privileged staging is mode 0700 and leaves no empty per-user parents."""
    session_path = tmp_path / "501" / "active_session.json"
    staging = tmp_path / "staging" / "501"
    staging.mkdir(parents=True, mode=0o755)
    fuse_path = allocate_privileged_fuse_path(session_path, 501)
    assert fuse_path.parent.stat().st_mode & 0o777 == 0o700
    fuse_path.rmdir()
    remove_empty_privileged_staging_parents(fuse_path, session_path)
    assert not staging.exists()
    assert not staging.parent.exists()


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
            "dislocker_ui.elevate.elevation_transaction",
            _fake_elevation_transaction(Path("/tmp/s.json"), Path("/tmp/l.log")),
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
