"""
Dependency discovery for dislocker-ui.

Overall purpose:
  Locate binaries needed for mount/unmount and report whether the core
  toolchain (including ntfs-3g) is available.

Inputs:
  Optional PATH overrides via the process environment (standard shutil.which).

Outputs:
  DepsStatus describing found paths and capability flags.

Requirements:
  Standard library only (shutil, dataclasses).
  On modern macOS, ntfs-3g is required for both read-only and read/write mounts
  (kernel mount_ntfs is unavailable).
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from shutil import which

_SYSTEM_TOOLS = {
    "hdiutil": "/usr/bin/hdiutil",
    "diskutil": "/usr/sbin/diskutil",
    "umount": "/sbin/umount",
}
_PRIVILEGED_TOOL_CANDIDATES = {
    "dislocker_fuse": ("/usr/local/sbin/dislocker-fuse", "/opt/local/sbin/dislocker-fuse"),
    "ntfs3g": ("/usr/local/sbin/ntfs-3g", "/opt/local/sbin/ntfs-3g"),
}


@dataclass(frozen=True)
class DepsStatus:
    """Snapshot of tools available on this Mac."""

    dislocker_fuse: str | None
    hdiutil: str | None
    diskutil: str | None
    umount: str | None
    ntfs3g: str | None

    @property
    def core_ok(self) -> bool:
        """True when the minimum mount toolchain (including ntfs-3g) is present."""
        return bool(
            self.dislocker_fuse and self.hdiutil and self.diskutil and self.umount and self.ntfs3g
        )

    @property
    def can_write(self) -> bool:
        """True when ntfs-3g is available for writable NTFS mounts."""
        return bool(self.ntfs3g)

    def missing_core(self) -> list[str]:
        """Return names of required tools that were not found."""
        mapping = {
            "dislocker-fuse": self.dislocker_fuse,
            "hdiutil": self.hdiutil,
            "diskutil": self.diskutil,
            "umount": self.umount,
            "ntfs-3g": self.ntfs3g,
        }
        return [name for name, path in mapping.items() if not path]


def discover_deps() -> DepsStatus:
    """
    Search PATH for dislocker and mount-related binaries.

    Also checks common Homebrew locations for ntfs-3g if not on PATH.
    """
    ntfs = which("ntfs-3g")
    if not ntfs:
        for candidate in (
            "/opt/homebrew/bin/ntfs-3g",
            "/usr/local/bin/ntfs-3g",
        ):
            from pathlib import Path

            if Path(candidate).is_file():
                ntfs = candidate
                break

    return DepsStatus(
        dislocker_fuse=which("dislocker-fuse") or which("dislocker"),
        hdiutil=which("hdiutil"),
        diskutil=which("diskutil"),
        umount=which("umount"),
        ntfs3g=ntfs,
    )


def discover_privileged_deps() -> DepsStatus:
    """Return only fixed, root-managed executable paths for the root helper.

    The privileged child must not inherit ``PATH`` or request-selected binary
    paths.  Third-party tools are deliberately accepted only from documented
    root-managed locations; a user-owned Homebrew installation is advisory for
    the GUI but is not safe to execute as root.
    """
    found: dict[str, str | None] = {}
    for name, value in _SYSTEM_TOOLS.items():
        found[name] = value if _is_trusted_executable(Path(value)) else None
    for name, candidates in _PRIVILEGED_TOOL_CANDIDATES.items():
        found[name] = next(
            (candidate for candidate in candidates if _is_trusted_executable(Path(candidate))), None
        )
    return DepsStatus(
        dislocker_fuse=found["dislocker_fuse"],
        hdiutil=found["hdiutil"],
        diskutil=found["diskutil"],
        umount=found["umount"],
        ntfs3g=found["ntfs3g"],
    )


def _is_trusted_executable(path: Path) -> bool:
    """True only for a trusted regular executable behind a trusted path."""
    try:
        entry = os.lstat(path)
        if stat.S_ISLNK(entry.st_mode):
            if entry.st_uid != 0:
                return False
            target = path.resolve(strict=True)
        else:
            target = path
        info = os.stat(target, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or not info.st_mode & 0o111
            or info.st_mode & 0o022
        ):
            return False
        if not _has_trusted_ancestors(path) or (
            target != path and not _has_trusted_ancestors(target)
        ):
            return False
    except OSError:
        return False
    return True


def _has_trusted_ancestors(path: Path) -> bool:
    """Return whether every directory leading to *path* is root-managed."""
    for parent in path.parents:
        parent_info = os.lstat(parent)
        if (
            stat.S_ISLNK(parent_info.st_mode)
            or not stat.S_ISDIR(parent_info.st_mode)
            or parent_info.st_uid != 0
            or parent_info.st_mode & 0o022
        ):
            return False
    return True
