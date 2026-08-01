"""
Focused unit tests for ntfs-3g option assembly and mount owner resolution.

Overall purpose:
  Cover Task 0 runner changes (always ntfs-3g; uid/gid/umask options) with
  mocked subprocess so CI does not need FUSE/ntfs-3g.

Requirements:
  pytest.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dislocker_ui.deps import DepsStatus
from dislocker_ui.ntfs_mount import mount_ntfs as _mount_ntfs
from dislocker_ui.ntfs_mount import ntfs3g_options as _ntfs3g_options
from dislocker_ui.ntfs_mount import resolve_mount_owner as _resolve_mount_owner
from dislocker_ui.runner import (
    MountRequest,
    UnlockMethod,
)


def _deps() -> DepsStatus:
    return DepsStatus(
        dislocker_fuse="/bin/dislocker-fuse",
        hdiutil="/bin/hdiutil",
        diskutil="/bin/diskutil",
        umount="/bin/umount",
        ntfs3g="/bin/ntfs-3g",
    )


def test_ntfs3g_options_readonly_includes_ro_and_umask() -> None:
    """RO mounts use -o ro plus ownership / allow_other / umask=077."""
    opts = _ntfs3g_options(readonly=True, uid=501, gid=20, volume_label="BitLocker")
    assert opts.startswith("ro,")
    assert "allow_other" in opts
    assert "local" in opts
    assert "uid=501" in opts
    assert "gid=20" in opts
    assert "umask=077" in opts
    assert "fmask=177" in opts
    assert "dmask=077" in opts
    assert "volname=BitLocker" in opts


def test_ntfs3g_options_rw_omits_ro() -> None:
    """RW mounts omit ro but keep mandatory umask and ownership."""
    opts = _ntfs3g_options(readonly=False, uid=501, gid=20, volume_label="X,Y")
    assert not opts.startswith("ro,")
    assert "ro," not in opts
    assert "umask=077" in opts
    assert "volname=X_Y" in opts  # comma sanitized


def test_resolve_mount_owner_explicit() -> None:
    """Explicit uid/gid from an elevated parent win."""
    assert _resolve_mount_owner(501, 20) == (501, 20)


def test_resolve_mount_owner_sudo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """SUDO_UID/SUDO_GID are used when valid and resolvable via pwd."""
    monkeypatch.setenv("SUDO_UID", "501")
    monkeypatch.setenv("SUDO_GID", "20")
    with patch("dislocker_ui.ntfs_mount._pwd_resolves", return_value=True):
        assert _resolve_mount_owner() == (501, 20)


def test_resolve_mount_owner_fallback_warns() -> None:
    """Missing SUDO_* falls back to 0/0 and logs a warning."""
    logs: list[str] = []
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("dislocker_ui.ntfs_mount._parse_sudo_id", return_value=None),
    ):
        # Clear SUDO vars explicitly for this process view
        import os

        for key in ("SUDO_UID", "SUDO_GID"):
            os.environ.pop(key, None)
        uid, gid = _resolve_mount_owner(log=logs.append)
    assert (uid, gid) == (0, 0)
    assert any("uid=0/gid=0" in line for line in logs)


def test_assert_mount_owner_mismatch(tmp_path: Path) -> None:
    """Ownership assert raises when mountpoint uid does not match."""
    from dislocker_ui.ntfs_mount import assert_mount_owner
    from dislocker_ui.runner import RunnerError

    mnt = tmp_path / "mnt"
    mnt.mkdir()
    with pytest.raises(RunnerError, match="ownership mismatch"):
        assert_mount_owner(mnt, uid=os.getuid() + 1, gid=None, log=lambda _m: None)

    """_mount_ntfs invokes ntfs-3g with assembled -o options (never kernel mount)."""
    req = MountRequest(
        volume="/dev/disk2s1",
        method=UnlockMethod.USER_PASSWORD,
        secret="secret",
        readonly=True,
        volume_label="TestVol",
    )
    mountpoint = tmp_path / "mnt"
    log = MagicMock()
    with patch("dislocker_ui.runner._run") as run:
        used = _mount_ntfs(_deps(), req, "/dev/disk999", mountpoint, log, uid=501, gid=20)

    assert used is True
    run.assert_called_once()
    cmd = run.call_args.args[0]
    assert cmd[0] == "/bin/ntfs-3g"
    assert cmd[1] == "/dev/disk999"
    assert cmd[2] == str(mountpoint)
    assert cmd[3] == "-o"
    assert "ro" in cmd[4]
    assert "uid=501" in cmd[4]
    assert "umask=077" in cmd[4]
    assert "mount" not in cmd[0]
