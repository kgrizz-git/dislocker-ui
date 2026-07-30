"""
Dependency discovery for dislocker-ui.

Overall purpose:
  Locate binaries needed for mount/unmount and report whether writable NTFS
  mounting is available via ntfs-3g.

Inputs:
  Optional PATH overrides via the process environment (standard shutil.which).

Outputs:
  DepsStatus describing found paths and capability flags.

Requirements:
  Standard library only (shutil, dataclasses).
"""

from __future__ import annotations

from dataclasses import dataclass
from shutil import which
from typing import Optional


@dataclass(frozen=True)
class DepsStatus:
    """Snapshot of tools available on this Mac."""

    dislocker_fuse: Optional[str]
    hdiutil: Optional[str]
    diskutil: Optional[str]
    mount: Optional[str]
    umount: Optional[str]
    ntfs3g: Optional[str]

    @property
    def core_ok(self) -> bool:
        """True when the minimum mount toolchain is present."""
        return bool(
            self.dislocker_fuse
            and self.hdiutil
            and self.diskutil
            and self.mount
            and self.umount
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
            "mount": self.mount,
            "umount": self.umount,
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
        mount=which("mount"),
        umount=which("umount"),
        ntfs3g=ntfs,
    )
