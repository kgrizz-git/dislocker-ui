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

from dataclasses import dataclass
from shutil import which


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
