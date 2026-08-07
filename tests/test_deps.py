"""
Unit tests for dependency discovery.

Overall purpose:
  Exercise DepsStatus helpers and discover_deps() with mocked shutil.which /
  filesystem checks so CI does not need macOS tools installed.

Requirements:
  pytest.
"""

from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dislocker_ui.deps import DepsStatus, discover_deps


def test_core_ok_without_ntfs3g() -> None:
    """core_ok is true without ntfs-3g so FAT/ExFAT mounts can proceed."""
    status = DepsStatus(
        dislocker_fuse="/opt/homebrew/bin/dislocker-fuse",
        hdiutil="/usr/bin/hdiutil",
        diskutil="/usr/sbin/diskutil",
        umount="/sbin/umount",
        ntfs3g=None,
    )
    assert status.core_ok is True
    assert status.can_write is False
    assert status.has_ntfs3g is False
    assert status.missing_core() == []


def test_core_ok_and_missing_core() -> None:
    """core_ok / missing_core reflect which required tools are present."""
    status = DepsStatus(
        dislocker_fuse="/opt/homebrew/bin/dislocker-fuse",
        hdiutil="/usr/bin/hdiutil",
        diskutil=None,
        umount="/sbin/umount",
        ntfs3g="/opt/homebrew/bin/ntfs-3g",
    )
    assert status.core_ok is False
    assert status.can_write is False
    assert status.has_ntfs3g is True
    assert status.missing_core() == ["diskutil"]


def test_can_write_requires_ntfs3g() -> None:
    """can_write is true only when core tools and ntfs-3g are both present."""
    status = DepsStatus(
        dislocker_fuse="x",
        hdiutil="x",
        diskutil="x",
        umount="x",
        ntfs3g="/opt/homebrew/bin/ntfs-3g",
    )
    assert status.core_ok is True
    assert status.can_write is True
    assert status.has_ntfs3g is True


def test_discover_deps_uses_which(monkeypatch: pytest.MonkeyPatch) -> None:
    """discover_deps maps which() results into DepsStatus fields."""

    mapping = {
        "dislocker-fuse": "/bin/dislocker-fuse",
        "hdiutil": "/bin/hdiutil",
        "diskutil": "/bin/diskutil",
        "umount": "/bin/umount",
        "ntfs-3g": "/bin/ntfs-3g",
        "dislocker": None,
    }

    def fake_which(name: str) -> str | None:
        return mapping.get(name)

    monkeypatch.setattr("dislocker_ui.deps.which", fake_which)
    with patch.object(Path, "is_file", return_value=False):
        status = discover_deps()

    assert status.dislocker_fuse == "/bin/dislocker-fuse"
    assert status.ntfs3g == "/bin/ntfs-3g"
    assert status.core_ok is True
    assert not hasattr(status, "mount")


def test_discover_deps_homebrew_ntfs_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ntfs-3g is not on PATH, known Homebrew paths are probed."""

    def fake_which(name: str) -> str | None:
        if name == "ntfs-3g":
            return None
        return f"/bin/{name}"

    monkeypatch.setattr("dislocker_ui.deps.which", fake_which)

    def fake_is_file(self: Path) -> bool:
        return str(self) == "/opt/homebrew/bin/ntfs-3g"

    with patch.object(Path, "is_file", fake_is_file):
        status = discover_deps()

    assert status.ntfs3g == "/opt/homebrew/bin/ntfs-3g"
    assert status.can_write is True
    assert status.core_ok is True


def test_privileged_dependency_rejects_writable_executable() -> None:
    """A fixed root path is unsafe when its executable file is writable."""
    from dislocker_ui.deps import _is_trusted_executable

    candidate = Path("/opt/local/sbin/dislocker-fuse")

    def fake_stat(path: str | Path, **_kwargs):
        if Path(path) == candidate:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o775, st_uid=0)
        return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)

    with (
        patch("dislocker_ui.deps.os.stat", side_effect=fake_stat),
        patch("dislocker_ui.deps.os.lstat", side_effect=fake_stat),
    ):
        assert _is_trusted_executable(candidate) is False


def test_privileged_dependency_accepts_root_owned_symlink_to_trusted_target() -> None:
    """A root-owned fixed link may point to a fully root-managed executable."""
    from dislocker_ui.deps import _is_trusted_executable

    candidate = Path("/usr/local/sbin/dislocker-fuse")
    target = Path("/opt/local/libexec/dislocker-fuse")

    def fake_lstat(path: str | Path, **_kwargs):
        if Path(path) == candidate:
            return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0)
        return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)

    def fake_stat(path: str | Path, **_kwargs):
        if Path(path) == target:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o755, st_uid=0)
        return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)

    with (
        patch("dislocker_ui.deps.os.lstat", side_effect=fake_lstat),
        patch("dislocker_ui.deps.os.stat", side_effect=fake_stat),
        patch.object(Path, "resolve", return_value=target),
    ):
        assert _is_trusted_executable(candidate) is True


def test_privileged_dependency_rejects_symlink_target_below_unsafe_parent() -> None:
    """A root-owned link cannot redirect root execution through an unsafe directory."""
    from dislocker_ui.deps import _is_trusted_executable

    candidate = Path("/usr/local/sbin/dislocker-fuse")
    target = Path("/opt/local/libexec/dislocker-fuse")

    def fake_lstat(path: str | Path, **_kwargs):
        current = Path(path)
        if current == candidate:
            return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0)
        if current == target.parent:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=501)
        return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)

    def fake_stat(path: str | Path, **_kwargs):
        if Path(path) == target:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o755, st_uid=0)
        return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)

    with (
        patch("dislocker_ui.deps.os.lstat", side_effect=fake_lstat),
        patch("dislocker_ui.deps.os.stat", side_effect=fake_stat),
        patch.object(Path, "resolve", return_value=target),
    ):
        assert _is_trusted_executable(candidate) is False


def test_privileged_dependency_rejects_user_owned_symlink() -> None:
    """The fixed executable entry itself must remain root-owned."""
    from dislocker_ui.deps import _is_trusted_executable

    candidate = Path("/usr/local/sbin/dislocker-fuse")
    with patch(
        "dislocker_ui.deps.os.lstat",
        return_value=SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=501),
    ):
        assert _is_trusted_executable(candidate) is False
