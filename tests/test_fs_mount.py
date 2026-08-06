"""
Unit tests for filesystem probe and FAT/ExFAT mount helpers.

Overall purpose:
  Cover BitLocker-To-Go post-decrypt routing (NTFS vs FAT vs ExFAT) without
  requiring a real disk image or FUSE.

Requirements:
  pytest.
"""

from __future__ import annotations

import plistlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dislocker_ui.fat_mount import mount_fat
from dislocker_ui.fs_probe import classify_device, resolve_mount_filesystem
from dislocker_ui.runner import MountRequest, RunnerError, UnlockMethod


def _plist(data: dict) -> bytes:
    return plistlib.dumps(data)


def test_classify_device_fat32() -> None:
    info = {
        "FilesystemType": "msdos",
        "FilesystemName": "MS-DOS FAT32",
        "Content": "None",
    }
    with patch("dislocker_ui.fs_probe._diskutil_info", return_value=info):
        assert classify_device("/usr/sbin/diskutil", "/dev/disk4") == "msdos"


def test_classify_device_ntfs() -> None:
    info = {"FilesystemType": "ntfs", "FilesystemName": "NTFS"}
    with patch("dislocker_ui.fs_probe._diskutil_info", return_value=info):
        assert classify_device("/usr/sbin/diskutil", "/dev/disk4") == "ntfs"


def test_classify_device_exfat() -> None:
    info = {"FilesystemName": "ExFAT", "FilesystemType": "exfat"}
    with patch("dislocker_ui.fs_probe._diskutil_info", return_value=info):
        assert classify_device("/usr/sbin/diskutil", "/dev/disk4") == "exfat"


def test_resolve_falls_back_to_partition() -> None:
    """Whole-disk node with no FS → use first classified partition."""

    def fake_info(_diskutil: str, device: str) -> dict:
        if device == "/dev/disk4":
            return {}
        if device == "/dev/disk4s1":
            return {"FilesystemType": "msdos", "FilesystemName": "MS-DOS FAT32"}
        return {}

    list_plist = _plist(
        {
            "AllDisksAndPartitions": [
                {
                    "DeviceIdentifier": "disk4",
                    "Partitions": [{"DeviceIdentifier": "disk4s1"}],
                }
            ]
        }
    )
    with (
        patch("dislocker_ui.fs_probe._diskutil_info", side_effect=fake_info),
        patch(
            "dislocker_ui.fs_probe.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=list_plist),
        ),
    ):
        device, kind = resolve_mount_filesystem("/usr/sbin/diskutil", "/dev/disk4")
    assert device == "/dev/disk4s1"
    assert kind == "msdos"


def test_resolve_unknown_raises() -> None:
    with (
        patch("dislocker_ui.fs_probe._diskutil_info", return_value={}),
        patch(
            "dislocker_ui.fs_probe.subprocess.run",
            return_value=MagicMock(returncode=1, stdout=b""),
        ),
        pytest.raises(RuntimeError, match="Could not detect"),
    ):
        resolve_mount_filesystem("/usr/sbin/diskutil", "/dev/disk9")


def test_mount_fat_invokes_mount_msdos(tmp_path: Path) -> None:
    """_mount_fat invokes mount_msdos with uid/gid and owner-only mode 700."""
    req = MountRequest(
        volume="/dev/disk2s1",
        method=UnlockMethod.USER_PASSWORD,
        secret="secret",
        readonly=True,
        volume_label="CMR",
    )
    mountpoint = tmp_path / "mnt"
    log = MagicMock()
    with patch("dislocker_ui.runner._run") as run:
        used = mount_fat(req, "/dev/disk999", mountpoint, log, kind="msdos", uid=501, gid=20)

    assert used is False
    cmd = run.call_args.args[0]
    assert cmd[0] == "/sbin/mount_msdos"
    assert cmd[1:7] == ["-u", "501", "-g", "20", "-m", "700"]
    assert cmd[7:9] == ["-o", "rdonly"]
    assert cmd[9:] == ["/dev/disk999", str(mountpoint)]


def test_mount_decrypted_volume_routes_fat() -> None:
    """Runner probes the attached image and dispatches to mount_fat for msdos."""
    from dislocker_ui.deps import DepsStatus
    from dislocker_ui.runner import _mount_decrypted_volume

    deps = DepsStatus(
        dislocker_fuse="/bin/dislocker-fuse",
        hdiutil="/bin/hdiutil",
        diskutil="/bin/diskutil",
        umount="/bin/umount",
        ntfs3g="/bin/ntfs-3g",
    )
    req = MountRequest(
        volume="/dev/disk2s1",
        method=UnlockMethod.USER_PASSWORD,
        secret="secret",
        readonly=True,
        volume_label="CMR",
    )
    logs: list[str] = []
    with (
        patch(
            "dislocker_ui.runner.resolve_mount_filesystem",
            return_value=("/dev/disk4", "msdos"),
        ),
        patch("dislocker_ui.runner._mount_fat", return_value=False) as fat,
        patch("dislocker_ui.runner._mount_ntfs") as ntfs,
    ):
        used = _mount_decrypted_volume(
            deps, req, "/dev/disk4", Path("/Volumes/CMR"), logs.append, uid=501, gid=20
        )
    assert used is False
    fat.assert_called_once()
    ntfs.assert_not_called()
    assert any("msdos" in line for line in logs)


def test_mount_fat_exfat_rw_omits_rdonly(tmp_path: Path) -> None:
    req = MountRequest(
        volume="/dev/disk2s1",
        method=UnlockMethod.USER_PASSWORD,
        secret="secret",
        readonly=False,
        volume_label="X",
    )
    mountpoint = tmp_path / "mnt"
    with patch("dislocker_ui.runner._run") as run:
        mount_fat(req, "/dev/disk8", mountpoint, lambda _m: None, kind="exfat", uid=501, gid=20)
    cmd = run.call_args.args[0]
    assert cmd[0] == "/sbin/mount_exfat"
    assert "-o" not in cmd
    assert "rdonly" not in cmd


def test_mount_fat_rejects_unknown_kind(tmp_path: Path) -> None:
    req = MountRequest(
        volume="/dev/disk2s1",
        method=UnlockMethod.USER_PASSWORD,
        secret="x",
        readonly=True,
        volume_label="Y",
    )
    with pytest.raises(RunnerError, match="Unsupported"):
        mount_fat(req, "/dev/disk1", tmp_path / "m", lambda _m: None, kind="zfs")


def test_mount_fat_rejects_non_physical_disk(tmp_path: Path) -> None:
    req = MountRequest(
        volume="/dev/disk2s1",
        method=UnlockMethod.USER_PASSWORD,
        secret="x",
        readonly=True,
        volume_label="Y",
    )
    with pytest.raises(RunnerError, match="non-physical"):
        mount_fat(
            req,
            "/dev/disk4; rm -rf /",
            tmp_path / "m",
            lambda _m: None,
            kind="msdos",
            uid=501,
            gid=20,
        )


def test_mount_fat_rejects_unsafe_mountpoint() -> None:
    req = MountRequest(
        volume="/dev/disk2s1",
        method=UnlockMethod.USER_PASSWORD,
        secret="x",
        readonly=True,
        volume_label="Y",
    )
    with pytest.raises(RunnerError, match="unsafe characters"):
        mount_fat(
            req,
            "/dev/disk1",
            Path("/Volumes/bad;id"),
            lambda _m: None,
            kind="msdos",
            uid=501,
            gid=20,
        )
