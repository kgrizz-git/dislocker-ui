"""
Tests for stale-session handling at mount time (plan D3).

Overall purpose:
  Verify a fully-stale session is cleared by the authoritative mount
  validator, still blocks while any target looks live or unknown, and
  the unprivileged pre-check only skips (never clears).

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
from dislocker_ui.mount_policy import privileged_staging_dir
from dislocker_ui.runner import (
    MountRequest,
    RunnerError,
    UnlockMethod,
    _validate_mount_request,
    mount_volume,
)
from dislocker_ui.session import MountSession, save_session

_NTFS_MOUNT = "/Volumes/DislockerUI-stale-probe"


def _deps() -> DepsStatus:
    return DepsStatus(
        dislocker_fuse="/bin/dislocker-fuse",
        hdiutil="/bin/hdiutil",
        diskutil="/bin/diskutil",
        umount="/bin/umount",
        ntfs3g="/bin/ntfs-3g",
    )


def _req(volume: str) -> MountRequest:
    return MountRequest(
        volume=volume,
        method=UnlockMethod.USER_PASSWORD,
        secret="x",
        readonly=True,
    )


def _stale_session(
    *,
    raw_disk: str = "/dev/disk9",
    ntfs_mount: str = _NTFS_MOUNT,
    fuse_mount: str | None = None,
) -> MountSession:
    """Build a validation-passing session whose targets are gone."""
    fuse_mount = fuse_mount or str(Path(tempfile.gettempdir()) / "dislocker-ui-gone")
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


def _stored_session(tmp_path: Path, session: MountSession) -> Path:
    """Write *session* to a temp session file; return its path."""
    path = tmp_path / "active_session.json"
    save_session(session, path)
    return path


def _stale_responder(
    *, table: str = "", info_images: list[dict] | None = None, info_rc: int = 0
) -> MagicMock:
    """Serve a fake mount table and hdiutil image list."""
    mount_result = MagicMock(returncode=0, stdout=table, stderr="")
    info_result = MagicMock(
        returncode=info_rc, stdout=plistlib.dumps({"images": info_images or []}), stderr=b""
    )
    ok = MagicMock(returncode=0, stdout="", stderr="")

    def _fake(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:1] == ["/sbin/mount"]:
            return mount_result
        if cmd[1:3] == ["info", "-plist"]:
            return info_result
        return ok

    return MagicMock(side_effect=_fake)


def _ignore_log(_message: str) -> None:
    """Discard log lines."""


@contextlib.contextmanager
def _volume(tmp_path: Path) -> Iterator[str]:
    """Create a real file to mount; yield its path."""
    target = tmp_path / "disk2s1"
    target.write_bytes(b"fake-bitlocker-volume")
    yield str(target)


def test_validate_mount_request_clears_stale_session(tmp_path: Path) -> None:
    """A fully-stale session is cleared and mount proceeds."""
    stored = _stored_session(tmp_path, _stale_session())
    logs: list[str] = []
    run = _stale_responder()
    with (
        _volume(tmp_path) as volume,
        patch("dislocker_ui.unmount_steps.subprocess.run", run),
    ):
        assert (
            _validate_mount_request(_req(volume), _deps(), logs.append, session_path=stored)
            == volume
        )
    assert not stored.exists()
    assert any("Stale session" in line for line in logs)


def test_validate_mount_request_clears_stale_root_session_path(tmp_path: Path) -> None:
    """The root child's canonical session path clears the same way."""
    session_path = tmp_path / "501" / "active_session.json"
    save_session(_stale_session(), session_path)
    run = _stale_responder()
    with (
        _volume(tmp_path) as volume,
        patch("dislocker_ui.unmount_steps.subprocess.run", run),
    ):
        assert (
            _validate_mount_request(
                _req(volume), _deps(), lambda _m: None, session_path=session_path
            )
            == volume
        )
    assert not session_path.exists()


def test_validate_mount_request_clears_stale_elevated_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The root child clears an elevated session whose targets are all gone."""
    volumes = tmp_path / "Volumes"
    volumes.mkdir()
    monkeypatch.setattr("dislocker_ui.mount_policy.VOLUMES_ROOT", volumes)
    session_path = tmp_path / "501" / "active_session.json"
    fuse = privileged_staging_dir(session_path, 501) / "session-test"
    fuse.mkdir(parents=True)  # is_privileged_fuse_path lstats the entry
    session = MountSession(
        volume="/dev/disk2s1",
        fuse_mount=str(fuse),
        dislocker_file=str(fuse / "dislocker-file"),
        raw_disk="/dev/disk9",
        ntfs_mount=str(volumes / "USB"),
        readonly=True,
        used_ntfs3g=True,
        elevated=True,
    )
    save_session(session, session_path)
    logs: list[str] = []
    run = _stale_responder()
    with (
        _volume(tmp_path) as volume,
        patch("dislocker_ui.unmount_steps.subprocess.run", run),
    ):
        assert (
            _validate_mount_request(_req(volume), _deps(), logs.append, session_path=session_path)
            == volume
        )
    assert not session_path.exists()
    assert any("Stale session" in line for line in logs)


def test_validate_mount_request_clears_empty_ntfs_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Self-heal removes the leftover empty mountpoint so labels don't shift."""
    volumes = tmp_path / "Volumes"
    volumes.mkdir()
    monkeypatch.setattr("dislocker_ui.mount_policy.VOLUMES_ROOT", volumes)
    ntfs = volumes / "DislockerUI"
    ntfs.mkdir()
    stored = _stored_session(tmp_path, _stale_session(ntfs_mount=str(ntfs)))
    run = _stale_responder()
    with (
        _volume(tmp_path) as volume,
        patch("dislocker_ui.unmount_steps.subprocess.run", run),
    ):
        _validate_mount_request(_req(volume), _deps(), lambda _m: None, session_path=stored)
    assert not stored.exists()
    assert not ntfs.exists()


def test_validate_mount_request_clears_dead_fuse_dir(tmp_path: Path) -> None:
    """Self-heal removes the leftover FUSE staging dir alongside the session."""
    fuse = Path(tempfile.mkdtemp(prefix="dislocker-ui-"))
    try:
        stored = _stored_session(tmp_path, _stale_session(fuse_mount=str(fuse)))
        run = _stale_responder()
        with (
            _volume(tmp_path) as volume,
            patch("dislocker_ui.unmount_steps.subprocess.run", run),
        ):
            _validate_mount_request(_req(volume), _deps(), lambda _m: None, session_path=stored)
        assert not stored.exists()
        assert not fuse.exists()
    finally:
        shutil.rmtree(fuse, ignore_errors=True)


def test_validate_mount_request_blocks_renumbered_image(tmp_path: Path) -> None:
    """An image attached under a new device number still blocks mounting."""
    fuse = Path(tempfile.mkdtemp(prefix="dislocker-ui-"))
    try:
        stored = _stored_session(
            tmp_path, _stale_session(fuse_mount=str(fuse), raw_disk="/dev/disk5")
        )
        entry = {
            "image-path": str(fuse / "dislocker-file"),
            "system-entities": [{"dev-entry": "/dev/disk7"}],
        }
        run = _stale_responder(info_images=[entry])
        with (
            _volume(tmp_path) as volume,
            patch("dislocker_ui.unmount_steps.subprocess.run", run),
            pytest.raises(RunnerError, match="already active"),
        ):
            _validate_mount_request(_req(volume), _deps(), lambda _m: None, session_path=stored)
        assert stored.exists()
    finally:
        shutil.rmtree(fuse, ignore_errors=True)


@pytest.mark.skipif(os.geteuid() == 0, reason="needs a non-root file owner")
def test_validate_mount_request_rejects_foreign_owned_file(tmp_path: Path) -> None:
    """Root never mounts over a session file owned by someone else."""
    stored = _stored_session(tmp_path, _stale_session())
    with (
        _volume(tmp_path) as volume,
        patch("os.geteuid", return_value=0),
        pytest.raises(RunnerError, match="not owned by root") as excinfo,
    ):
        _validate_mount_request(_req(volume), _deps(), lambda _m: None, session_path=stored)
    assert "left untouched" in str(excinfo.value)
    assert stored.exists()


def test_validate_mount_request_blocks_live_session(tmp_path: Path) -> None:
    """A session with mounted targets still blocks mounting."""
    session = _stale_session()
    stored = _stored_session(tmp_path, session)
    table = f"/dev/disk9 on {session.ntfs_mount} (ntfs, local)\n/dev/disk9 on {session.fuse_mount} (msdos)\n"
    run = _stale_responder(table=table)
    deps = _deps()
    with _volume(tmp_path) as volume, patch("dislocker_ui.unmount_steps.subprocess.run", run):
        req = _req(volume)
        with pytest.raises(RunnerError, match="already active"):
            _validate_mount_request(req, deps, _ignore_log, session_path=stored)
    assert stored.exists()


def test_validate_mount_request_blocks_when_state_unknown(tmp_path: Path) -> None:
    """An unreadable table or image list is never treated as stale."""
    stored = _stored_session(tmp_path, _stale_session())
    deps = _deps()
    with _volume(tmp_path) as volume:
        req = _req(volume)
        with (
            patch("dislocker_ui.unmount_steps._mount_table", return_value=None),
            pytest.raises(RunnerError, match="already active"),
        ):
            _validate_mount_request(req, deps, _ignore_log, session_path=stored)
        run = _stale_responder(info_rc=1)
        with (
            patch("dislocker_ui.unmount_steps.subprocess.run", run),
            pytest.raises(RunnerError, match="already active"),
        ):
            _validate_mount_request(req, deps, _ignore_log, session_path=stored)
    assert stored.exists()


def test_validate_mount_request_blocks_invalid_session(tmp_path: Path) -> None:
    """A session failing path validation is never stale."""
    stored = _stored_session(tmp_path, _stale_session(raw_disk="bogus"))
    run = _stale_responder()
    deps = _deps()
    with _volume(tmp_path) as volume, patch("dislocker_ui.unmount_steps.subprocess.run", run):
        req = _req(volume)
        with pytest.raises(RunnerError, match="already active"):
            _validate_mount_request(req, deps, _ignore_log, session_path=stored)
    assert stored.exists()


def test_mount_volume_precheck_passes_stale_to_elevation(tmp_path: Path) -> None:
    """A stale-looking session skips the raise but is never cleared here."""
    stored = _stored_session(tmp_path, _stale_session())
    sentinel = MagicMock(spec=MountSession)
    run = _stale_responder()
    with (
        patch("dislocker_ui.elevate.needs_elevation", return_value=True),
        patch("dislocker_ui.runner.active_session_path_for_user", return_value=stored),
        patch("dislocker_ui.runner.legacy_session_present", return_value=False),
        patch("dislocker_ui.elevate.elevation_transaction") as txn,
        patch("dislocker_ui.elevate.run_elevated_mount", return_value=sentinel) as elev,
        patch("dislocker_ui.unmount_steps.subprocess.run", run),
    ):
        txn.return_value.__enter__.return_value = (stored, tmp_path / "op.log")
        assert mount_volume(_req("/dev/disk2s1"), _deps(), lambda _m: None) is sentinel
    elev.assert_called_once()
    assert stored.exists()


def test_mount_volume_precheck_blocks_live_session(tmp_path: Path) -> None:
    """A live-looking session still raises before any admin prompt."""
    session = _stale_session()
    stored = _stored_session(tmp_path, session)
    table = f"/dev/disk9 on {session.ntfs_mount} (ntfs, local)\n"
    run = _stale_responder(table=table)
    req, deps = _req("/dev/disk2s1"), _deps()
    with (
        patch("dislocker_ui.elevate.needs_elevation", return_value=True),
        patch("dislocker_ui.runner.active_session_path_for_user", return_value=stored),
        patch("dislocker_ui.elevate.run_elevated_mount") as elev,
        patch("dislocker_ui.elevate.elevation_transaction") as txn,
        patch("dislocker_ui.unmount_steps.subprocess.run", run),
        pytest.raises(RunnerError, match="already active"),
    ):
        mount_volume(req, deps, _ignore_log)
    elev.assert_not_called()
    txn.assert_not_called()
    assert stored.exists()
