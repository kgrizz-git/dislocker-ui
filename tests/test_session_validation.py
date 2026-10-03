"""
Tests for unmount session path validation (plan D1b).

Overall purpose:
  Verify crafted non-elevated sessions are rejected before any unmount
  side effect, well-formed sessions pass through, and the root owner
  check rejects foreign-owned session files.

Requirements:
  pytest.
"""

from __future__ import annotations

import contextlib
import os
import plistlib
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dislocker_ui.deps import DepsStatus
from dislocker_ui.mount_policy import session_paths_error
from dislocker_ui.runner import RunnerError, unmount_volume
from dislocker_ui.session import MountSession, load_session, save_session, session_owner_mismatch


def _deps() -> DepsStatus:
    return DepsStatus(
        dislocker_fuse="/bin/dislocker-fuse",
        hdiutil="/bin/hdiutil",
        diskutil="/bin/diskutil",
        umount="/bin/umount",
        ntfs3g="/bin/ntfs-3g",
    )


def _session(
    *,
    fuse_mount: str,
    ntfs_mount: str = "/Volumes/DislockerUI-stale-probe",
    raw_disk: str = "/dev/disk9",
    elevated: bool = False,
) -> MountSession:
    """Build a session with valid shapes unless overridden."""
    return MountSession(
        volume="/dev/disk2s1",
        fuse_mount=fuse_mount,
        dislocker_file=fuse_mount + "/dislocker-file",
        raw_disk=raw_disk,
        ntfs_mount=ntfs_mount,
        readonly=True,
        used_ntfs3g=True,
        elevated=elevated,
    )


def _stored_session(tmp_path: Path, session: MountSession) -> Path:
    """Write *session* to a temp session file; return its path."""
    path = tmp_path / "active_session.json"
    save_session(session, path)
    return path


def _unmount_session_file(session_path: Path, logs: list[str]) -> None:
    """Run the in-process unmount path against an explicit session file."""
    with patch("dislocker_ui.elevate.needs_elevation", return_value=False):
        unmount_volume(_deps(), logs.append, session_path=session_path)


@contextlib.contextmanager
def _fake_root_owned(path: Path) -> Iterator[None]:
    """Report *path* as uid-0-owned through os.open/os.fstat (root simulation)."""
    real_open, real_fstat = os.open, os.fstat
    owned: set[int] = set()

    def _open(file: object, flags: int, *args: object, **kwargs: object) -> int:
        fd = real_open(file, flags, *args, **kwargs)  # type: ignore[arg-type]
        if isinstance(file, str | bytes | os.PathLike) and os.path.abspath(file) == str(path):
            owned.add(fd)
        return fd

    def _fstat(fd: int) -> os.stat_result:
        st = real_fstat(fd)
        if fd in owned:
            st = os.stat_result(
                (
                    st.st_mode,
                    st.st_ino,
                    st.st_dev,
                    st.st_nlink,
                    0,
                    st.st_gid,
                    st.st_size,
                    st.st_atime,
                    st.st_mtime,
                    st.st_ctime,
                )
            )
        return st

    with patch("os.open", side_effect=_open), patch("os.fstat", side_effect=_fstat):
        yield


@pytest.mark.skipif(os.geteuid() == 0, reason="needs a non-root file owner")
def test_unmount_as_root_accepts_root_looking_session(tmp_path: Path) -> None:
    """Running as root, a root-owned session file unmounts normally."""
    fuse = Path(tempfile.mkdtemp(prefix="dislocker-ui-"))
    stored = _stored_session(tmp_path, _session(fuse_mount=str(fuse)))
    table = f"/dev/disk9 on {fuse} (msdos, local, noowners)\n"
    images = [
        {
            "image-path": str(fuse / "dislocker-file"),
            "system-entities": [{"dev-entry": "/dev/disk9"}],
        }
    ]
    mount_result = MagicMock(returncode=0, stdout=table, stderr="")
    info_result = MagicMock(returncode=0, stdout=plistlib.dumps({"images": images}), stderr=b"")
    ok = MagicMock(returncode=0, stdout="", stderr="")
    run = MagicMock(
        side_effect=lambda cmd, **kwargs: (
            mount_result
            if cmd[:1] == ["/sbin/mount"]
            else info_result
            if cmd[1:3] == ["info", "-plist"]
            else ok
        )
    )
    logs: list[str] = []
    try:
        with (
            patch("os.geteuid", return_value=0),
            patch("dislocker_ui.elevate.needs_elevation", return_value=False),
            patch("dislocker_ui.runner.legacy_session_present", return_value=False),
            patch("dislocker_ui.unmount_steps.subprocess.run", run),
            _fake_root_owned(stored),
        ):
            unmount_volume(_deps(), logs.append, session_path=stored)
    finally:
        shutil.rmtree(fuse, ignore_errors=True)
    assert not stored.exists()
    assert ["/bin/umount", str(fuse)] in [c.args[0] for c in run.call_args_list]
    assert ["/bin/hdiutil", "detach", "/dev/disk9"] in [c.args[0] for c in run.call_args_list]
    assert any("Unmounted successfully" in line for line in logs)


@contextlib.contextmanager
def _guarded_boundaries() -> Iterator[tuple[MagicMock, MagicMock, MagicMock]]:
    """Patch subprocess/shutil/rmdir boundaries; yield the mocks."""
    run = MagicMock()
    rmtree = MagicMock()
    rmdir = MagicMock()
    with (
        patch("subprocess.run", run),
        patch("shutil.rmtree", rmtree),
        patch.object(Path, "rmdir", rmdir),
    ):
        yield run, rmtree, rmdir


def _assert_no_side_effects(
    stored: Path, run: MagicMock, rmtree: MagicMock, rmdir: MagicMock
) -> None:
    """The session file is retained and no cleanup primitive ran."""
    assert stored.exists()
    run.assert_not_called()
    rmtree.assert_not_called()
    rmdir.assert_not_called()


def test_unmount_rejects_fuse_mount_outside_tempdir(tmp_path: Path) -> None:
    """A FUSE path outside the tempdir raises before any side effect."""
    stored = _stored_session(tmp_path, _session(fuse_mount=str(tmp_path / "dislocker-ui-sneaky")))
    with (
        _guarded_boundaries() as (run, rmtree, rmdir),
        pytest.raises(RunnerError, match="temporary directory") as excinfo,
    ):
        _unmount_session_file(stored, [])
    assert "left untouched" in str(excinfo.value)
    _assert_no_side_effects(stored, run, rmtree, rmdir)


def test_unmount_rejects_ntfs_mount_outside_volumes(tmp_path: Path) -> None:
    """An NTFS mountpoint outside /Volumes raises before any side effect."""
    fuse = Path(tempfile.mkdtemp(prefix="dislocker-ui-"))
    try:
        stored = _stored_session(
            tmp_path, _session(fuse_mount=str(fuse), ntfs_mount="/tmp/evil-mount")
        )
        with (
            _guarded_boundaries() as (run, rmtree, rmdir),
            pytest.raises(RunnerError, match="mountpoint is unsafe"),
        ):
            _unmount_session_file(stored, [])
        _assert_no_side_effects(stored, run, rmtree, rmdir)
    finally:
        shutil.rmtree(fuse, ignore_errors=True)


def test_unmount_rejects_bad_raw_disk(tmp_path: Path) -> None:
    """A non-device raw disk raises before any side effect."""
    fuse = Path(tempfile.mkdtemp(prefix="dislocker-ui-"))
    try:
        stored = _stored_session(tmp_path, _session(fuse_mount=str(fuse), raw_disk="/dev/rdisk2"))
        with (
            _guarded_boundaries() as (run, rmtree, rmdir),
            pytest.raises(RunnerError, match="raw disk"),
        ):
            _unmount_session_file(stored, [])
        _assert_no_side_effects(stored, run, rmtree, rmdir)
    finally:
        shutil.rmtree(fuse, ignore_errors=True)


def test_unmount_valid_session_passes_validation(tmp_path: Path) -> None:
    """A well-formed session runs the real steps and clears state."""
    fuse = Path(tempfile.mkdtemp(prefix="dislocker-ui-"))
    stored = _stored_session(tmp_path, _session(fuse_mount=str(fuse)))
    logs: list[str] = []
    with _guarded_boundaries() as (run, rmtree, rmdir):
        ok = MagicMock(returncode=0, stdout="", stderr="")
        info = MagicMock(returncode=0, stdout=plistlib.dumps({"images": []}), stderr=b"")
        run.side_effect = lambda cmd, **kwargs: info if cmd[1:3] == ["info", "-plist"] else ok
        _unmount_session_file(stored, logs)
    assert not stored.exists()
    assert run.called
    rmtree.assert_called_once_with(fuse)
    rmdir.assert_not_called()
    assert any("Unmounted successfully" in line for line in logs)
    shutil.rmtree(fuse, ignore_errors=True)


def test_session_paths_error_dispatches_by_session_kind() -> None:
    """Elevated sessions use the privileged check; others use the tempdir check."""
    elevated = _session(fuse_mount="x", raw_disk="bogus", elevated=True)
    assert "Privileged session" in (session_paths_error(elevated, None) or "")
    plain = _session(fuse_mount="x")
    assert "temporary directory" in (session_paths_error(plain, None) or "")


def test_session_paths_error_accepts_well_formed_session() -> None:
    """A tempdir FUSE child, /Volumes mountpoint, and disk node pass."""
    fuse = Path(tempfile.mkdtemp(prefix="dislocker-ui-"))
    try:
        assert session_paths_error(_session(fuse_mount=str(fuse)), None) is None
    finally:
        shutil.rmtree(fuse, ignore_errors=True)


def test_load_session_rejects_foreign_owner(tmp_path: Path) -> None:
    """require_owner returns None for files owned by someone else."""
    stored = _stored_session(tmp_path, _session(fuse_mount="x"))
    owner = os.lstat(stored).st_uid
    assert load_session(stored, require_owner=owner) is not None
    assert load_session(stored, require_owner=owner + 1) is None
    assert load_session(stored) is not None


@pytest.mark.skipif(os.geteuid() == 0, reason="needs a non-root file owner")
def test_unmount_as_root_rejects_foreign_owned_session(tmp_path: Path) -> None:
    """Running as root, a session file owned by someone else is distrusted."""
    stored = _stored_session(tmp_path, _session(fuse_mount="x"))
    assert os.lstat(stored).st_uid != 0
    with (
        patch("os.geteuid", return_value=0),
        patch("dislocker_ui.elevate.needs_elevation", return_value=False),
        patch("dislocker_ui.runner.legacy_session_present", return_value=False),
        pytest.raises(RunnerError, match="not owned by root"),
    ):
        unmount_volume(_deps(), lambda _m: None, session_path=stored)
    assert stored.exists()


def test_session_owner_mismatch_reports_foreign_files(tmp_path: Path) -> None:
    """The helper flags existing foreign-owned files, nothing else."""
    stored = _stored_session(tmp_path, _session(fuse_mount="x"))
    owner = os.lstat(stored).st_uid
    assert session_owner_mismatch(stored, owner) is False
    assert session_owner_mismatch(stored, owner + 1) is True
    assert session_owner_mismatch(tmp_path / "missing.json", owner + 1) is False
