"""
Disk enumeration helpers for dislocker-ui.

Overall purpose:
  List block devices that a user might select as a BitLocker volume.

Inputs:
  None (queries the local system via diskutil).

Outputs:
  A list of DiskEntry values (identifier + human-readable summary).

Requirements:
  macOS diskutil; standard library (subprocess, plistlib).
"""

from __future__ import annotations

import plistlib
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DiskEntry:
    """One selectable disk or partition."""

    device: str
    summary: str


def list_disk_entries() -> list[DiskEntry]:
    """
    Return whole disks and partitions from `diskutil list -plist`.

    Failures raise RuntimeError with stderr context. BitLocker detection is
    not attempted here — any partition may be selected manually.
    """
    proc = subprocess.run(
        ["diskutil", "list", "-plist"],
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(err or "diskutil list failed")

    data: dict[str, Any] = plistlib.loads(proc.stdout)
    entries: list[DiskEntry] = []

    for disk in data.get("AllDisksAndPartitions", []):
        disk_id = disk.get("DeviceIdentifier")
        if disk_id:
            size = _fmt_size(disk.get("Size"))
            entries.append(
                DiskEntry(
                    device=f"/dev/{disk_id}",
                    summary=f"/dev/{disk_id}  (disk, {size})",
                )
            )
        for part in disk.get("Partitions", []) or []:
            part_id = part.get("DeviceIdentifier")
            if not part_id:
                continue
            name = part.get("VolumeName") or part.get("Content") or "partition"
            size = _fmt_size(part.get("Size"))
            entries.append(
                DiskEntry(
                    device=f"/dev/{part_id}",
                    summary=f"/dev/{part_id}  ({name}, {size})",
                )
            )

    return entries


def _fmt_size(num_bytes: Any) -> str:
    """Format a byte count for short UI display."""
    try:
        n = float(num_bytes)
    except (TypeError, ValueError):
        return "?"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while n >= 1024 and idx < len(units) - 1:
        n /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(n)} {units[idx]}"
    return f"{n:.1f} {units[idx]}"
