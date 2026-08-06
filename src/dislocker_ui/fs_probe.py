"""
Filesystem probe helpers for decrypted BitLocker images.

Overall purpose:
  After dislocker-fuse + hdiutil attach, classify the attached raw disk as
  NTFS, MS-DOS (FAT), or ExFAT so the mount pipeline can pick ntfs-3g vs
  mount_msdos / mount_exfat.

Inputs:
  A ``/dev/diskN`` (or slice) path and the absolute ``diskutil`` binary.

Outputs:
  A ``(device, kind)`` pair where *kind* is ``ntfs``, ``msdos``, or ``exfat``.
  Raises ``RuntimeError`` when the filesystem cannot be classified.

Requirements:
  macOS diskutil; standard library (plistlib, subprocess).
"""

from __future__ import annotations

import plistlib
import re
import subprocess
from typing import Any

_KIND_NTFS = "ntfs"
_KIND_MSDOS = "msdos"
_KIND_EXFAT = "exfat"

_NTFS_HINTS = ("ntfs",)
_MSDOS_HINTS = ("msdos", "fat32", "fat16", "fat12", "ms-dos", "dos_fat")
_EXFAT_HINTS = ("exfat",)
_WHOLE_DISK_RE = re.compile(r"/dev/(disk\d+)(?:s\d+)?")


def resolve_mount_filesystem(diskutil: str, raw_disk: str) -> tuple[str, str]:
    """
    Return ``(device_to_mount, filesystem_kind)`` for an attached raw disk.

    Tries *raw_disk* first. When that node has no recognizable filesystem
    (common when hdiutil exposes a partitioned image), probes child slices
    ``diskNs1``, ``diskNs2``, … from ``diskutil list -plist``.
    """
    candidates = [raw_disk, *_partition_devices(diskutil, raw_disk)]
    seen: set[str] = set()
    for device in candidates:
        if device in seen:
            continue
        seen.add(device)
        kind = classify_device(diskutil, device)
        if kind is not None:
            return device, kind
    raise RuntimeError(
        f"Could not detect NTFS/FAT/ExFAT on {raw_disk} "
        "(unsupported filesystem after BitLocker decrypt)."
    )


def classify_device(diskutil: str, device: str) -> str | None:
    """Return filesystem kind for *device*, or None when unknown/absent."""
    info = _diskutil_info(diskutil, device)
    return _classify_info(info)


def _diskutil_info(diskutil: str, device: str) -> dict[str, Any]:
    """Parse ``diskutil info -plist`` for *device*; empty dict on failure."""
    return _diskutil_plist(diskutil, "info", device)


def _partition_devices(diskutil: str, raw_disk: str) -> list[str]:
    """Return ``/dev/diskNsM`` children of the whole disk behind *raw_disk*."""
    whole = _whole_disk_identifier(raw_disk)
    if whole is None:
        return []
    data = _diskutil_plist(diskutil, "list", f"/dev/{whole}")
    return _slice_paths_for_whole(data, whole)


def _whole_disk_identifier(raw_disk: str) -> str | None:
    """Extract ``diskN`` from a ``/dev/diskN`` or ``/dev/diskNsM`` path."""
    match = _WHOLE_DISK_RE.fullmatch(raw_disk)
    return match.group(1) if match else None


def _diskutil_plist(diskutil: str, subcmd: str, device: str) -> dict[str, Any]:
    """Run ``diskutil <subcmd> -plist <device>`` and return a dict (or {})."""
    proc = subprocess.run(
        [diskutil, subcmd, "-plist", device],
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        return {}
    try:
        data = plistlib.loads(proc.stdout)
    except plistlib.InvalidFileException:
        return {}
    return data if isinstance(data, dict) else {}


def _slice_paths_for_whole(data: dict[str, Any], whole: str) -> list[str]:
    """Collect ``/dev/…`` paths for partitions of *whole* from a list plist."""
    out: list[str] = []
    for disk in data.get("AllDisksAndPartitions", []) or []:
        for part_id in _partition_ids_from_disk_entry(disk, whole):
            out.append(f"/dev/{part_id}")
    return out


def _partition_ids_from_disk_entry(disk: object, whole: str) -> list[str]:
    """Return DeviceIdentifier strings for partitions of *whole* on *disk*."""
    if not isinstance(disk, dict) or disk.get("DeviceIdentifier") != whole:
        return []
    ids: list[str] = []
    for part in disk.get("Partitions", []) or []:
        if not isinstance(part, dict):
            continue
        part_id = part.get("DeviceIdentifier")
        if isinstance(part_id, str) and part_id:
            ids.append(part_id)
    return ids


def _classify_info(info: dict[str, Any]) -> str | None:
    """Map diskutil plist fields to a mount kind."""
    blobs = [
        info.get("FilesystemType"),
        info.get("FilesystemName"),
        info.get("Content"),
        info.get("Personality"),
    ]
    text = " ".join(str(b).lower() for b in blobs if b)
    if not text.strip():
        return None
    if any(h in text for h in _NTFS_HINTS):
        return _KIND_NTFS
    if any(h in text for h in _EXFAT_HINTS):
        return _KIND_EXFAT
    if any(h in text for h in _MSDOS_HINTS):
        return _KIND_MSDOS
    return None
