"""
Unit tests for dependency discovery.

Overall purpose:
  Exercise DepsStatus helpers and discover_deps() with mocked shutil.which /
  filesystem checks so CI does not need macOS tools installed.

Requirements:
  pytest.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from dislocker_ui.deps import DepsStatus, discover_deps


def test_core_ok_and_missing_core() -> None:
    """core_ok / missing_core reflect which required tools are present."""
    status = DepsStatus(
        dislocker_fuse="/opt/homebrew/bin/dislocker-fuse",
        hdiutil="/usr/bin/hdiutil",
        diskutil=None,
        mount="/sbin/mount",
        umount="/sbin/umount",
        ntfs3g=None,
    )
    assert status.core_ok is False
    assert status.can_write is False
    assert status.missing_core() == ["diskutil"]


def test_can_write_when_ntfs3g_present() -> None:
    """can_write is true only when ntfs-3g was found."""
    status = DepsStatus(
        dislocker_fuse="x",
        hdiutil="x",
        diskutil="x",
        mount="x",
        umount="x",
        ntfs3g="/opt/homebrew/bin/ntfs-3g",
    )
    assert status.core_ok is True
    assert status.can_write is True


def test_discover_deps_uses_which(monkeypatch: pytest.MonkeyPatch) -> None:
    """discover_deps maps which() results into DepsStatus fields."""

    mapping = {
        "dislocker-fuse": "/bin/dislocker-fuse",
        "hdiutil": "/bin/hdiutil",
        "diskutil": "/bin/diskutil",
        "mount": "/bin/mount",
        "umount": "/bin/umount",
        "ntfs-3g": None,
        "dislocker": None,
    }

    def fake_which(name: str) -> str | None:
        return mapping.get(name)

    monkeypatch.setattr("dislocker_ui.deps.which", fake_which)
    with patch.object(Path, "is_file", return_value=False):
        status = discover_deps()

    assert status.dislocker_fuse == "/bin/dislocker-fuse"
    assert status.ntfs3g is None
    assert status.core_ok is True


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
