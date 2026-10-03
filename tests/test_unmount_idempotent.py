"""
Tests for idempotent unmount (plan D1 + D6).

Overall purpose:
  Verify unmount succeeds when targets are already gone, still attempts
  umount when liveness is uncertain, retains state on real failure, and
  warns (instead of erroring) on a non-empty unmounted /Volumes dir.

Requirements:
  pytest.
"""

from __future__ import annotations

import contextlib
import errno
import os
import plistlib
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dislocker_ui.deps import DepsStatus
from dislocker_ui.runner import RunnerError, unmount_volume
from dislocker_ui.session import MountSession, save_session
from dislocker_ui.unmount_steps import _is_mounted, _mounted_paths

_NTFS_MOUNT = "/Volumes/DislockerUI-stale-probe"
_UMOUNT = "/bin/umount"


def _deps() -> DepsStatus:
    return DepsStatus(
        dislocker_fuse="/bin/dislocker-fuse",
        hdiutil="/bin/hdiutil",
        diskutil="/bin/diskutil",
        umount=_UMOUNT,
        ntfs3g="/bin/ntfs-3g",
    )


def _session(
    *,
    fuse_mount: str,
    ntfs_mount: str = _NTFS_MOUNT,
    raw_disk: str = "/dev/disk9",
) -> MountSession:
    """Build a valid non-elevated session for the idempotency tests."""
    return MountSession(
        volume="/dev/disk2s1",
        fuse_mount=fuse_mount,
        dislocker_file=fuse_mount + "/dislocker-file",
        raw_disk=raw_disk,
        ntfs_mount=ntfs_mount,
        readonly=True,
        used_ntfs3g=True,
        elevated=False,
    )


@contextlib.contextmanager
def _fuse_dir() -> Iterator[Path]:
    """Create a real tempdir dislocker-ui-* dir; remove it afterwards."""
    fuse = Path(tempfile.mkdtemp(prefix="dislocker-ui-"))
    try:
        yield fuse
    finally:
        shutil.rmtree(fuse, ignore_errors=True)


def _stored_session(tmp_path: Path, session: MountSession) -> Path:
    """Write *session* to a temp session file; return its path."""
    path = tmp_path / "active_session.json"
    save_session(session, path)
    return path


def _unmount_session_file(session_path: Path, logs: list[str]) -> None:
    """Run the in-process unmount path against an explicit session file."""
    with patch("dislocker_ui.elevate.needs_elevation", return_value=False):
        unmount_volume(_deps(), logs.append, session_path=session_path)


def _mount_responder(
    *, table: str = "", mount_rc: int = 0, other_rc: int = 0, other_err: str = ""
) -> MagicMock:
    """Serve a fake /sbin/mount table and an empty hdiutil image list."""
    mount_result = MagicMock(returncode=mount_rc, stdout=table, stderr="")
    info_result = MagicMock(returncode=0, stdout=plistlib.dumps({"images": []}), stderr=b"")
    other_result = MagicMock(returncode=other_rc, stdout="", stderr=other_err)

    def _fake(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:1] == ["/sbin/mount"]:
            return mount_result
        if cmd[1:3] == ["info", "-plist"]:
            return info_result
        return other_result

    return MagicMock(side_effect=_fake)


def test_mounted_paths_parses_mount_table() -> None:
    """Mount-table lines yield their mountpoints; malformed lines are skipped."""
    table = (
        "/dev/disk1s1 on / (apfs, local, journaled)\n"
        "devfs on /dev (devfs, local, nobrowse)\n"
        "/dev/disk9 on /Volumes/My Passport (msdos, local, noowners)\n"
        "garbage without separators\n"
        "/dev/disk9 on /weird-but-no-parens\n"
    )
    with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=table, stderr="")):
        assert _mounted_paths() == {"/", "/dev", "/Volumes/My Passport"}


def test_mounted_paths_returns_none_when_listing_fails() -> None:
    """A failed, hung, or unrunnable /sbin/mount reports an unknown table."""
    with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="x")):
        assert _mounted_paths() is None
    with patch("subprocess.run", side_effect=OSError("no mount")):
        assert _mounted_paths() is None
    expired = subprocess.TimeoutExpired(["/sbin/mount"], 10)
    with patch("subprocess.run", side_effect=expired) as run:
        assert _mounted_paths() is None
    assert run.call_args.kwargs["timeout"] == 10


def test_is_mounted_matches_exact_and_aliased_paths() -> None:
    """Exact hits and /tmp vs /private/tmp aliases count as mounted."""
    table = {"/private/tmp/dislocker-ui-x"}
    realpath = lambda p: "/private/tmp" if p == "/tmp" else p  # noqa: E731  # nosec B108 - fixture path, never created
    with (
        patch("dislocker_ui.unmount_steps._mounted_paths", return_value=table),
        patch("os.path.realpath", side_effect=realpath),
    ):
        assert _is_mounted("/private/tmp/dislocker-ui-x")
        assert _is_mounted("/tmp/dislocker-ui-x")  # nosec B108 - fixture path, never created
        assert not _is_mounted("/tmp/dislocker-ui-other")  # nosec B108 - fixture path, never created


def test_is_mounted_assumes_mounted_when_table_unavailable() -> None:
    """An unknown table fails toward attempting umount, not toward clearing."""
    with patch("dislocker_ui.unmount_steps._mounted_paths", return_value=None):
        assert _is_mounted("/Volumes/Anything")


def test_unmount_stale_session_succeeds_without_umount(tmp_path: Path) -> None:
    """Nothing mounted: unmount clears state and never invokes umount."""
    with _fuse_dir() as fuse:
        stored = _stored_session(tmp_path, _session(fuse_mount=str(fuse)))
        logs: list[str] = []
        run = _mount_responder(table="/dev/disk1s1 on / (apfs, local, journaled)\n")
        with patch("dislocker_ui.unmount_steps.subprocess.run", run):
            _unmount_session_file(stored, logs)
    assert not stored.exists()
    assert any(f"already unmounted: {_NTFS_MOUNT}" in line for line in logs)
    assert any(f"already unmounted: {fuse}" in line for line in logs)
    assert any("Unmounted successfully" in line for line in logs)
    assert [c for c in run.call_args_list if c.args[0][0] == _UMOUNT] == []


def test_unmount_dead_fuse_still_attempts_umount(tmp_path: Path) -> None:
    """A mount-table hit wins over hostile stat results; umount is attempted."""
    with _fuse_dir() as fuse:
        stored = _stored_session(tmp_path, _session(fuse_mount=str(fuse)))
        table = f"/dev/disk9 on {fuse} (msdos, local, noowners)\n"
        run = _mount_responder(table=table)
        real_lstat = os.lstat

        def _lstat(path: object, *args: object, **kwargs: object) -> object:
            if str(path) == str(fuse):
                raise OSError(errno.ENXIO, "Device not configured")
            return real_lstat(path, *args, **kwargs)  # type: ignore[arg-type]

        with (
            patch("dislocker_ui.unmount_steps.subprocess.run", run),
            patch("os.lstat", side_effect=_lstat),
            patch("os.path.ismount", return_value=False),
            # rmtree itself lstats its target; the cleanup primitive is
            # scaffolding here, not the behavior under test.
            patch("shutil.rmtree"),
        ):
            _unmount_session_file(stored, [])
    assert not stored.exists()
    assert [_UMOUNT, str(fuse)] in [c.args[0] for c in run.call_args_list]


def test_unmount_attempts_umount_when_mount_table_fails(tmp_path: Path) -> None:
    """An unreadable table treats every target as possibly mounted."""
    with _fuse_dir() as fuse:
        session = _session(fuse_mount=str(fuse))
        stored = _stored_session(tmp_path, session)
        run = _mount_responder(mount_rc=1)
        with patch("dislocker_ui.unmount_steps.subprocess.run", run):
            _unmount_session_file(stored, [])
    assert not stored.exists()
    attempted = [c.args[0] for c in run.call_args_list]
    assert [_UMOUNT, session.ntfs_mount] in attempted
    assert [_UMOUNT, session.fuse_mount] in attempted


def test_unmount_live_failure_retains_session(tmp_path: Path) -> None:
    """A mounted volume that refuses umount keeps state for retry."""
    with _fuse_dir() as fuse:
        session = _session(fuse_mount=str(fuse))
        stored = _stored_session(tmp_path, session)
        table = f"/dev/disk9 on {session.ntfs_mount} (ntfs, local, noowners)\n"
        run = _mount_responder(table=table, other_rc=1, other_err="busy")
        with (
            patch("dislocker_ui.unmount_steps.subprocess.run", run),
            pytest.raises(RunnerError, match="retained"),
        ):
            _unmount_session_file(stored, [])
    assert stored.exists()


def test_unmount_removes_empty_volumes_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty stale /Volumes dir is removed and state clears."""
    volumes = tmp_path / "Volumes"
    volumes.mkdir()
    monkeypatch.setattr("dislocker_ui.mount_policy.VOLUMES_ROOT", volumes)
    ntfs = volumes / "DislockerUI"
    ntfs.mkdir()
    with _fuse_dir() as fuse:
        stored = _stored_session(tmp_path, _session(fuse_mount=str(fuse), ntfs_mount=str(ntfs)))
        run = _mount_responder()
        with patch("dislocker_ui.unmount_steps.subprocess.run", run):
            _unmount_session_file(stored, [])
    assert not stored.exists()
    assert not ntfs.exists()


def test_unmount_refuses_reassigned_ntfs_device(tmp_path: Path) -> None:
    """A mountpoint now held by another disk retains state without umount."""
    with _fuse_dir() as fuse:
        session = _session(fuse_mount=str(fuse))
        stored = _stored_session(tmp_path, session)
        table = f"/dev/disk5 on {session.ntfs_mount} (apfs, local, journaled)\n"
        run = _mount_responder(table=table)
        with (
            patch("dislocker_ui.unmount_steps.subprocess.run", run),
            pytest.raises(RunnerError, match="different disk"),
        ):
            _unmount_session_file(stored, [])
    assert stored.exists()
    assert [c for c in run.call_args_list if c.args[0][0] == _UMOUNT] == []


def test_unmount_allows_own_partition_and_fuse_devices(tmp_path: Path) -> None:
    """The recorded disk, its partitions, and FUSE devices still unmount."""
    cases = [
        "/dev/disk9",  # the recorded raw disk itself
        "/dev/disk9s2",  # a partition of the recorded disk
        "dislocker-fuse@macfuse0",  # FUSE device strings carry no disk number
    ]
    for device in cases:
        with _fuse_dir() as fuse:
            session = _session(fuse_mount=str(fuse))
            stored = _stored_session(tmp_path, session)
            table = f"{device} on {session.ntfs_mount} (msdos, local, noowners)\n"
            run = _mount_responder(table=table)
            with patch("dislocker_ui.unmount_steps.subprocess.run", run):
                _unmount_session_file(stored, [])
        assert not stored.exists(), device
        assert [_UMOUNT, session.ntfs_mount] in [c.args[0] for c in run.call_args_list], device


def test_unmount_nonempty_unmounted_volumes_dir_warns_and_clears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale non-empty dir that is not mounted warns instead of wedging."""
    volumes = tmp_path / "Volumes"
    volumes.mkdir()
    monkeypatch.setattr("dislocker_ui.mount_policy.VOLUMES_ROOT", volumes)
    ntfs = volumes / "USB"
    ntfs.mkdir()
    (ntfs / "leftover.txt").write_text("stale contents")
    with _fuse_dir() as fuse:
        stored = _stored_session(tmp_path, _session(fuse_mount=str(fuse), ntfs_mount=str(ntfs)))
        logs: list[str] = []
        run = _mount_responder()
        with patch("dislocker_ui.unmount_steps.subprocess.run", run):
            _unmount_session_file(stored, logs)
    assert not stored.exists()
    assert ntfs.is_dir()  # contents are never deleted automatically
    assert any("leaving non-empty unmounted directory" in line for line in logs)
    assert any("USB" in line for line in logs)


def test_unmount_mounted_nonempty_dir_retains_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-empty dir that is still mounted keeps retry semantics."""
    volumes = tmp_path / "Volumes"
    volumes.mkdir()
    monkeypatch.setattr("dislocker_ui.mount_policy.VOLUMES_ROOT", volumes)
    ntfs = volumes / "USB"
    ntfs.mkdir()
    (ntfs / "leftover.txt").write_text("busy volume")
    with _fuse_dir() as fuse:
        session = _session(fuse_mount=str(fuse), ntfs_mount=str(ntfs))
        stored = _stored_session(tmp_path, session)
        table = f"/dev/disk9 on {ntfs} (msdos, local, noowners)\n/dev/disk9 on {fuse} (exfat)\n"
        run = _mount_responder(table=table)
        with (
            patch("dislocker_ui.unmount_steps.subprocess.run", run),
            pytest.raises(RunnerError, match="not empty"),
        ):
            _unmount_session_file(stored, [])
    assert stored.exists()
